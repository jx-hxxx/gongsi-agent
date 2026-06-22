"""3-트랙 요약: 사전요약(precompute) 생성·저장.

원리: 요약은 LLM 생성이라 느리다 → 적재 시점에 미리 만들어 summary_<corp_code> 에 저장.
질문이 오면 검색해서 '꺼내기만' 한다(app/services/chat._run_summary_turn).

트랙 분기 (공시 1건 단위):
  - 작은 공시 (원문 ≤ small_disclosure_chars  또는  목차 없음) → 원문 통째로 1개 요약
  - 큰 공시   (그 외, 목차 있음)                              → 목차(섹션)별 요약 N개
정량(특정 수치) 질문은 요약을 쓰지 않고 항상 RAG → 이 모듈과 무관.
"""
from __future__ import annotations

from app.agents import crew
from app.config import get_settings
from app.rag.chunker import split_into_sections
from app.rag.vectorstore import get_vector_store, summary_collection


def _group_sections(
    sections: list[tuple], target: int, max_groups: int
) -> list[tuple[str | None, str]]:
    """잘게 쪼개진 섹션들을 ~target 글자 묶음으로 합친다(묶음 수 ≤ max_groups).

    사업보고서가 수백 개 micro-섹션으로 쪼개지는 문제 보정 → LLM 호출 수를 제한.
    각 묶음의 제목은 그 묶음의 첫 섹션 제목을 대표로 쓴다.
    """
    def merge(size: int) -> list[tuple[str | None, str]]:
        groups: list[tuple[str | None, str]] = []
        cur_title = None
        cur: list[str] = []
        cur_len = 0
        for title, body in sections:
            if not cur:
                cur_title = title
            cur.append(body)
            cur_len += len(body)
            if cur_len >= size:
                groups.append((cur_title, "\n".join(cur)))
                cur, cur_len, cur_title = [], 0, None
        if cur:
            groups.append((cur_title, "\n".join(cur)))
        return groups

    groups = merge(target)
    if len(groups) > max_groups:
        total = sum(len(b) for _, b in sections)
        groups = merge(max(target, total // max_groups + 1))
    return groups


def build_summary_items(
    corp_name: str, rcept_no: str, report_nm: str, rcept_dt: str, full_text: str
) -> list[dict]:
    """공시 1건 → 요약 item 목록 [{id, text, metadata}]. (LLM 호출 발생)"""
    settings = get_settings()
    full_text = (full_text or "").strip()
    if not full_text:
        return []

    sections = split_into_sections(full_text)
    base = {
        "corp_name": corp_name,
        "rcept_no": rcept_no,
        "report_nm": report_nm,
        "rcept_dt": rcept_dt,
    }

    # 작은 공시: 원문이 짧거나 목차(섹션)가 사실상 없으면 통째로 1개 요약
    is_small = (
        len(full_text) <= settings.small_disclosure_chars
        or len(sections) <= 1
    )
    if is_small:
        s = crew.summarize_text(full_text, label=report_nm)
        if not s:
            return []
        return [{
            "id": f"{rcept_no}-sum-000",
            "text": s,
            "metadata": {**base, "section_title": "전체", "kind": "full"},
        }]

    # 큰 공시: 잘게 쪼개진 섹션을 묶음으로 합친 뒤(호출 수 제한) 묶음별 요약
    groups = _group_sections(
        sections, settings.summary_section_target_chars, settings.summary_max_sections
    )
    items: list[dict] = []
    i = 0
    for title, body in groups:
        body = (body or "").strip()
        if len(body) < settings.min_section_chars:
            continue
        s = crew.summarize_text(body, label=title or "")
        if not s:
            continue
        items.append({
            "id": f"{rcept_no}-sum-{i:03d}",
            "text": s,
            "metadata": {**base, "section_title": title or f"섹션 {i}", "kind": "section"},
        })
        i += 1
    return items


def build_and_store(
    corp_code: str, corp_name: str, rcept_no: str,
    report_nm: str, rcept_dt: str, full_text: str,
) -> int:
    """공시 1건의 사전요약을 만들어 summary_<corp_code> 에 저장(idempotent). 저장 개수 반환."""
    items = build_summary_items(corp_name, rcept_no, report_nm, rcept_dt, full_text)
    if not items:
        return 0
    store = get_vector_store()
    coll = summary_collection(corp_code)
    store.delete_summaries_for(coll, rcept_no)  # 재실행 시 중복 방지
    return store.index_summaries(coll, items)
