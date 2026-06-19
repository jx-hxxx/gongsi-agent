"""검색 후보 재정렬(rerank) — 금융 공시 표 기반 정량 질의 정확도 보강.

팀원(vectorstore 개선분)의 도메인 가점/노이즈 감점 아이디어를 **일반화**해 이식.
- 회사명·연도(삼성전자/2025/제57기) 하드코딩 제거 → 모든 기업·연도에 동작
- 발행주식총수·유통주식수·연결대상 종속기업수·자기주식 등 표 기반 숫자 질의에 가점
- 안 물어본 배당/성과급/내부거래 표는 감점
- expected_phrase(정답) 미사용 → 운영에서 그대로 사용 가능

vector 후보 rows([{id, doc, meta, distance, score}])를 받아 rerank_score 기준 재정렬.
"""
from __future__ import annotations

import re
import unicodedata

# 검색에 의미 없는 말투/단위 토큰 (회사명·연도 같은 고유값은 넣지 않음 → 일반화)
STOPWORDS = {
    "기준", "몇", "얼마", "인가요", "인가", "주세요", "알려줘", "알려주세요",
    "사업보고서", "보고서", "당기", "말", "수", "개", "주", "원", "퍼센트",
}

# 도메인 동의어 확장 (질문 토큰 → 관련 표현)
DOMAIN_EXPANSIONS = {
    "연결대상": {"연결대상", "종속기업", "종속회사", "연결", "기말"},
    "종속기업": {"연결대상", "종속기업", "종속회사", "주요 종속회사", "기말"},
    "종속회사": {"연결대상", "종속기업", "종속회사", "주요 종속회사", "기말"},
    "보통주": {"보통주", "주식", "발행주식", "발행주식총수", "자기주식", "의결권"},
    "우선주": {"우선주", "주식", "발행주식", "발행주식총수", "자기주식", "의결권"},
    "발행주식총수": {"발행주식총수", "발행주식", "주식총수", "주식의 총수"},
    "유통주식수": {"유통주식수", "유통주식", "자기주식", "의결권", "발행주식총수"},
    "자기주식수": {"자기주식수", "자기주식", "보유주식", "의결권"},
    "자기주식": {"자기주식", "자사주", "보유주식", "취득", "처분", "소각"},
    "의결권": {"의결권", "행사", "제한", "주식수", "자기주식"},
    "자본금": {"자본금", "보통주자본금", "우선주자본금", "합계"},
    "수주": {"수주", "계약", "계약금액", "공급계약"},
    "계약": {"수주", "계약", "계약금액", "공급계약"},
}

IMPORTANT_PHRASES = [
    "연결대상 종속기업", "연결대상 종속회사", "주요 종속회사", "비상장 종속기업",
    "보통주 발행주식총수", "우선주 발행주식총수", "보통주 유통주식수", "우선주 유통주식수",
    "보통주 자기주식수", "의결권 행사 가능", "의결권 행사가 제한",
    "합계 자본금", "보통주 자본금", "우선주 자본금", "계약금액",
]


def _norm(s) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    return re.sub(r"\s+", "", s).lower()


def _tokens(s) -> set[str]:
    if s is None:
        return set()
    s = unicodedata.normalize("NFKC", str(s)).lower()
    toks: set[str] = set()
    for tok in re.findall(r"[가-힣A-Za-z0-9.%]+", s):
        tok = tok.strip().lower()
        if not tok or tok in STOPWORDS or len(tok) < 2:
            continue
        toks.add(tok)
    compact = _norm(s)
    for phrase in IMPORTANT_PHRASES:
        if _norm(phrase) in compact:
            toks.add(phrase)
    expanded = set(toks)
    for tok in list(toks):
        if tok in DOMAIN_EXPANSIONS:
            expanded.update(DOMAIN_EXPANSIONS[tok])
    return expanded


def _has(hay_norm: str, needles: tuple[str, ...]) -> bool:
    return any(_norm(n) in hay_norm for n in needles if n)


# ===== 질문 의도 판정 =====
def _ask_issue_total(q: str) -> bool:
    return _has(q, ("발행주식총수", "발행주식의총수", "발행주식수", "주식발행총수")) or (
        "발행" in q and "주식" in q and "총수" in q
    )


def _ask_circulating(q: str) -> bool:
    return _has(q, ("유통주식수", "기말유통주식수", "유통주식"))


def _ask_subsidiary(q: str) -> bool:
    return _has(q, ("연결대상종속기업", "연결대상종속회사", "종속기업수", "종속회사수")) or (
        "연결대상" in q and ("종속기업" in q or "종속회사" in q)
    )


def _ask_buyback(q: str) -> bool:
    return _has(q, ("자기주식", "자사주", "주식소각", "취득결정", "처분결정"))


# 연도/회사 비의존 일반 기준일 표현
_AS_OF_TERMS = ("보고기간종료일현재", "기말", "보고서작성기준일", "보고서작성기준일현재")


def _domain_boost(q_norm: str, doc_norm: str, sec_norm: str) -> float:
    """질문 유형별 도메인 가점 (연도/회사 하드코딩 없음)."""
    scores = []

    if _ask_issue_total(q_norm):
        s = 0.0
        if _has(sec_norm, ("주식의총수", "발행주식의총수", "발행주식총수", "자본금", "자본금변동추이", "자본금변동사항")):
            s += 0.40
        if _has(doc_norm, ("보통주발행주식총수", "보통주발행주식수", "보통주발행주식의총수")):
            s += 0.65
        elif "보통주" in doc_norm and _has(doc_norm, ("발행주식총수", "발행주식의총수", "발행주식수", "회사가발행한보통주의수")):
            s += 0.55
        elif _has(doc_norm, ("발행주식총수", "발행주식의총수", "발행주식수")):
            s += 0.40
        if _has(doc_norm, _AS_OF_TERMS + ("단위:주",)):
            s += 0.10
        # "발행할 주식의 총수" = 정관상 한도(정답 아님) → 감점
        if _has(sec_norm, ("발행할주식의총수",)):
            s -= 0.65
        if _has(doc_norm, ("발행할주식의총수", "발행가능주식총수")) and not _has(
            doc_norm, ("발행주식총수", "발행주식의총수", "보통주발행주식총수")
        ):
            s -= 0.25
        scores.append(max(s, 0.0))

    if _ask_circulating(q_norm):
        s = 0.0
        if _has(sec_norm, ("유통주식수", "기말유통주식수", "주식의총수")):
            s += 0.35
        if _has(doc_norm, ("보통주유통주식수", "기말유통주식수", "현재유통주식수")):
            s += 0.65
        elif "보통주" in doc_norm and "유통주식수" in doc_norm:
            s += 0.55
        elif "유통주식수" in doc_norm:
            s += 0.40
        if _has(doc_norm, ("자기주식취득", "자기주식", "단위:주")):
            s += 0.12
        scores.append(max(s, 0.0))

    if _ask_subsidiary(q_norm):
        s = 0.0
        if _has(sec_norm, ("연결대상종속회사현황", "연결대상종속기업", "연결대상종속회사", "연결회사의개요")):
            s += 0.45
        if _has(doc_norm, ("연결대상종속기업은", "연결대상종속회사는", "개의종속기업", "연결대상으로하고", "전년말대비")):
            s += 0.55
        scores.append(max(s, 0.0))

    if _ask_buyback(q_norm):
        s = 0.0
        if _has(sec_norm, ("자기주식", "자기주식처분결정", "자기주식취득결정", "처분예정주식", "취득예정주식", "소각예정금액")):
            s += 0.35
        if _has(doc_norm, ("자기주식", "자사주", "취득결정", "처분결정", "처분예정주식", "취득예정주식", "주식소각", "소각", "보유수량")):
            s += 0.45
        if _has(doc_norm, ("보통주", "단위:주", "발행주식총수대비")):
            s += 0.15
        scores.append(max(s, 0.0))

    return max(scores) if scores else 0.0


def _noise_penalty(q_norm: str, doc_norm: str, sec_norm: str) -> float:
    """질문 의도와 어긋나는 청크 감점 (배당/성과급/내부거래 등)."""
    all_norm = sec_norm + "\n" + doc_norm
    penalty = 0.0
    dividend = ("주당배당금", "주당배당률", "배당수익률", "배당금총액", "배당받을주식", "중간배당", "기말배당")
    generic = ("내부거래", "내부회계관리제도", "매출원가", "금융비용", "이연법인세", "영업활동현금흐름", "감사업무")
    perf = ("주식기준보상", "성과급", "장기성과급", "performancestockunits", "psu", "가득조건")

    if _ask_issue_total(q_norm) or _ask_circulating(q_norm):
        if _has(all_norm, dividend):
            penalty += 0.90 if _ask_issue_total(q_norm) else 0.65
        if _has(all_norm, generic):
            penalty += 0.30
        if _has(all_norm, perf):
            penalty += 0.45
        if _ask_issue_total(q_norm) and _has(sec_norm, ("발행할주식의총수",)):
            penalty += 0.75

    if _ask_subsidiary(q_norm):
        if _has(all_norm, ("배당", "발행주식", "유통주식", "감사보고서")):
            penalty += 0.20

    if _ask_buyback(q_norm):
        if _has(all_norm, ("주당배당금", "매출원가", "이연법인세", "영업활동현금흐름")):
            penalty += 0.30

    return min(max(penalty, 0.0), 1.0)


def _phrase_score(q_norm: str, doc_norm: str) -> float:
    hits = total = 0
    for phrase in IMPORTANT_PHRASES:
        p = _norm(phrase)
        if p and p in q_norm:
            total += 1
            if p in doc_norm:
                hits += 1
    return hits / total if total else 0.0


def rerank_rows(query: str, rows: list[dict]) -> list[dict]:
    """vector 후보를 도메인 가점/노이즈 감점/어휘/섹션/핵심구 기준으로 재정렬.

    각 row 에 rerank_score 를 달고 내림차순 정렬해 반환.
    숫자형 표 질의가 아니면 domain/penalty가 0이라 사실상 vector 점수 순서를 따른다.
    """
    if not rows:
        return []
    q_terms = _tokens(query)
    q_norm = _norm(query)
    max_base = max(float(r.get("score") or 0.0) for r in rows) or 1.0

    for r in rows:
        doc = r.get("doc") or ""
        sec = str((r.get("meta") or {}).get("section_title") or "")
        doc_terms = _tokens(doc)
        sec_terms = _tokens(sec)
        lexical = len(q_terms & doc_terms) / max(len(q_terms), 1) if q_terms else 0.0
        section = len(q_terms & sec_terms) / max(len(q_terms), 1) if q_terms else 0.0
        doc_norm = _norm(doc)
        sec_norm = _norm(sec)
        base = float(r.get("score") or 0.0) / max_base
        domain = _domain_boost(q_norm, doc_norm, sec_norm)
        penalty = _noise_penalty(q_norm, doc_norm, sec_norm)
        r["rerank_score"] = (
            0.28 * base + 0.18 * lexical + 0.10 * section
            + 0.10 * _phrase_score(q_norm, doc_norm)
            + 0.46 * domain - 0.42 * penalty
        )
        r["domain_score"] = domain

    rows.sort(
        key=lambda r: (float(r.get("rerank_score") or 0.0), float(r.get("domain_score") or 0.0), float(r.get("score") or 0.0)),
        reverse=True,
    )
    return rows
