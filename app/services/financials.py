"""재무결합 — DART 정형 재무데이터를 근거(Citation)로 변환.

공시 본문 텍스트로는 수치(당분기/누적·연결/별도·단위)가 모호한데, 재무제표 API는
계정명·당기/전기·연결/별도가 구조화돼 있어 정확하다. 이를 Citation 으로 만들어
QA 의 근거에 합쳐(retrieve-then-read) 수치 질문 정확도를 높인다.
"""
from __future__ import annotations

from app.data import dart
from app.schemas.disclosure import Citation

# 결합할 핵심 계정 (손익 + 재무상태)
KEY_ACCOUNTS = ["매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계"]


def _fmt(amount: str | None) -> str:
    try:
        return f"{int(amount):,}"
    except (ValueError, TypeError):
        return amount or "-"


def get_financial_citations(corp_code: str) -> list[Citation]:
    """최근 사업보고서의 주요계정을 Citation 리스트로. 연결(CFS) 우선, 없으면 별도(OFS)."""
    year, rows = dart.recent_financials(corp_code)
    if not rows:
        return []

    picked: dict[str, dict] = {}
    for r in rows:
        nm = r.get("account_nm", "")
        if nm not in KEY_ACCOUNTS:
            continue
        # 연결(CFS) 우선 선택
        if nm not in picked or (r.get("fs_div") == "CFS" and picked[nm].get("fs_div") != "CFS"):
            picked[nm] = r

    cits: list[Citation] = []
    for nm in KEY_ACCOUNTS:
        r = picked.get(nm)
        if not r:
            continue
        rcept = r.get("rcept_no", "") or ""
        quote = (
            f"{nm} — 당기({r.get('thstrm_nm', '당기')}) {_fmt(r.get('thstrm_amount'))}원, "
            f"전기({r.get('frmtrm_nm', '전기')}) {_fmt(r.get('frmtrm_amount'))}원"
        )
        cits.append(
            Citation(
                chunk_id=f"fin-{nm}-{r.get('fs_div', '')}",
                section_title=f"재무제표 주요계정({r.get('fs_nm', '')})",
                quote=quote,
                score=None,
                rcept_no=rcept or None,
                report_nm=f"{year} 사업보고서 재무제표",
                rcept_dt=(rcept[:8] if rcept else None),
            )
        )
    return cits
