"""거시지표 조회 서비스 (캐시 + lazy-fill).

공시 발생일 기준 거시지표(환율·기준금리·KOSPI)를 ECOS 에서 가져온다.
과거값은 변하지 않으므로 날짜당 1회만 호출하고 SQLite 에 캐시한다.
"""
from __future__ import annotations

from app.data import ecos
from app.storage import db


def has_value(snapshot: dict) -> bool:
    """지표 중 실제 값이 하나라도 있으면 True (전부 실패면 캐시/결합하지 않으려고)."""
    inds = (snapshot or {}).get("indicators", {})
    return any(isinstance(v, dict) and v.get("value") is not None for v in inds.values())


def get_macro(as_of: str) -> dict:
    """as_of(YYYYMMDD) 기준 거시 스냅샷. 캐시에 있으면 그대로, 없으면 ECOS 호출 후 저장.

    전부 실패한 스냅샷은 캐시하지 않는다 (나중에 재시도 가능하게).
    """
    cached = db.get_macro_cache(as_of)
    if cached is not None:
        return cached

    snapshot = ecos.macro_snapshot(as_of)
    if has_value(snapshot):
        db.set_macro_cache(as_of, snapshot)
    return snapshot


def format_macro(snapshot: dict) -> str:
    """Macro Agent 프롬프트/표시에 넣을 사람이 읽는 형식."""
    inds = (snapshot or {}).get("indicators", {})
    labels = {
        "usd_krw": "원/달러 환율",
        "base_rate": "한국은행 기준금리",
        "market_rate": "시장금리(국고채 3년)",
        "kospi": "KOSPI",
    }
    lines = []
    for key, label in labels.items():
        v = inds.get(key)
        if isinstance(v, dict) and v.get("value") is not None:
            unit = v.get("unit", "") or ""
            lines.append(f"- {label}: {v['value']}{unit} (기준일 {v.get('time', '')})")
    return "\n".join(lines) or "(거시지표 조회 실패)"
