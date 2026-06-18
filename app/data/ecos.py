"""한국은행 ECOS 연동 (보조 데이터: 환율/기준금리/주가지수).

확정 통계표·항목 코드 (실 API 확인 완료):
  환율(원/달러 매매기준율) : 731Y001 / 0000001 (일)
  한국은행 기준금리         : 722Y001 / 0101000 (일)
  KOSPI 지수               : 802Y001 / 0001000 (일)
  KOSDAQ 지수              : 802Y001 / 0089000 (일)
  시장금리(국고채 3년)      : 817Y002 / 010200000 (일)

공시 발생일 기준으로 그 시점의 거시지표를 조회해 Macro Agent 가 결합 해석한다.
호출 한도가 있으므로 받아서 캐시하는 편이 안전하다.
"""
from __future__ import annotations

import httpx

from app.config import get_settings

BASE = "https://ecos.bok.or.kr/api"

# (통계표코드, 항목코드, 주기)
USD_KRW = ("731Y001", "0000001", "D")     # 원/달러 매매기준율
BASE_RATE = ("722Y001", "0101000", "D")   # 한국은행 기준금리
KOSPI = ("802Y001", "0001000", "D")       # KOSPI 지수
KOSDAQ = ("802Y001", "0089000", "D")      # KOSDAQ 지수
MARKET_RATE = ("817Y002", "010200000", "D")  # 시장금리 — 국고채(3년)


def fetch_statistic(
    stat_code: str,
    cycle: str,
    start: str,
    end: str,
    *,
    item_code1: str | None = None,
    rows: int = 100,
) -> list[dict]:
    """ECOS 통계 시계열 조회.

    cycle: 'D'(일) 'M'(월) 'Q'(분기) 'A'(년)
    start/end: 주기에 맞는 형식 (일=YYYYMMDD, 월=YYYYMM, 분기=YYYYQn)
    """
    key = get_settings().ecos_api_key or "sample"
    path = ["StatisticSearch", key, "json", "kr", "1", str(rows),
            stat_code, cycle, start, end]
    if item_code1:
        path.append(item_code1)
    url = f"{BASE}/" + "/".join(path)

    with httpx.Client(timeout=20) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()

    if "StatisticSearch" not in data:
        err = data.get("RESULT", {})
        raise RuntimeError(f"ECOS 오류: {err.get('CODE')} {err.get('MESSAGE')}")
    return data["StatisticSearch"].get("row", [])


def _latest_on_or_before(spec: tuple[str, str, str], end: str, lookback_days: int = 14) -> dict | None:
    """end(YYYYMMDD) 시점 또는 그 직전의 최신 값 1건."""
    from datetime import datetime, timedelta

    code, item, cyc = spec
    end_dt = datetime.strptime(end, "%Y%m%d")
    start = (end_dt - timedelta(days=lookback_days)).strftime("%Y%m%d")
    rows = fetch_statistic(code, cyc, start, end, item_code1=item, rows=100)
    return rows[-1] if rows else None


def macro_snapshot(date: str) -> dict:
    """공시 발생일(YYYYMMDD) 기준 거시지표 스냅샷.

    각 지표는 해당일 또는 직전 영업일 값을 사용한다.
    보조 데이터이므로 일부 실패해도 예외를 던지지 않고 누락 표시만 한다.
    """
    out: dict = {"as_of": date, "indicators": {}}
    targets = {
        "usd_krw": USD_KRW,
        "base_rate": BASE_RATE,
        "market_rate": MARKET_RATE,
        "kospi": KOSPI,
    }
    for name, spec in targets.items():
        try:
            row = _latest_on_or_before(spec, date)
            if row:
                out["indicators"][name] = {
                    "value": row["DATA_VALUE"],
                    "time": row["TIME"],
                    "unit": row.get("UNIT_NAME", ""),
                    "name": row.get("ITEM_NAME1", ""),
                }
        except Exception as e:  # 보조 데이터 → 실패해도 진행
            out["indicators"][name] = {"error": str(e)}
    return out
