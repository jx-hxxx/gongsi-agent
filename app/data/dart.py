"""OpenDART 연동 (주 데이터).

- list_disclosures: 공시 검색(접수번호 찾기용)
- fetch_document_text: 접수번호로 공시 원문(XML)을 받아 평문 텍스트로 변환

문서 다운로드 API는 ZIP(내부 XML) 을 반환한다. DART XML 의 태그를 제거해
사람이 읽는 본문만 추출한다.
"""
from __future__ import annotations

import io
import re
import zipfile

import httpx

from app.config import get_settings

BASE = "https://opendart.fss.or.kr/api"


class DartError(RuntimeError):
    pass


def _api_key() -> str:
    key = get_settings().dart_api_key
    if not key:
        raise DartError("DART_API_KEY 가 설정되지 않았습니다 (.env 확인).")
    return key


def list_disclosures(
    *,
    corp_code: str | None = None,
    bgn_de: str | None = None,
    end_de: str | None = None,
    pblntf_ty: str | None = None,
    page_no: int = 1,
    page_count: int = 100,
) -> dict:
    """공시 목록 조회. 전체 응답(dict)을 반환 — list/total_count/total_page 포함.

    pblntf_ty: 공시유형 코드 A~J (없으면 전체)
    """
    params = {
        "crtfc_key": _api_key(),
        "page_no": page_no,
        "page_count": page_count,
    }
    if corp_code:
        params["corp_code"] = corp_code
    if bgn_de:
        params["bgn_de"] = bgn_de
    if end_de:
        params["end_de"] = end_de
    if pblntf_ty:
        params["pblntf_ty"] = pblntf_ty

    with httpx.Client(timeout=20) as client:
        resp = client.get(f"{BASE}/list.json", params=params)
        resp.raise_for_status()
        data = resp.json()

    status = data.get("status")
    if status not in ("000", "013"):  # 013 = 데이터 없음
        raise DartError(f"DART list 오류: {status} {data.get('message')}")
    return data


# corpCode 매핑 캐시 경로
_CORPCODE_CACHE = "./data/corpcode.json"


def download_corp_codes() -> list[dict]:
    """전체 기업 고유번호(corp_code) 목록을 받아 캐시한다.

    corpCode.xml API → ZIP(CORPCODE.xml) → [{corp_code, corp_name, stock_code}]
    """
    import json
    import os
    import xml.etree.ElementTree as ET

    if os.path.exists(_CORPCODE_CACHE):
        with open(_CORPCODE_CACHE, encoding="utf-8") as f:
            return json.load(f)

    params = {"crtfc_key": _api_key()}
    with httpx.Client(timeout=60) as client:
        resp = client.get(f"{BASE}/corpCode.xml", params=params)
        resp.raise_for_status()
        content = resp.content
    if content[:2] != b"PK":
        raise DartError(f"corpCode 다운로드 실패: {content[:200]!r}")

    rows: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        xml = zf.read(zf.namelist()[0])
    root = ET.fromstring(xml)
    for el in root.iter("list"):
        rows.append(
            {
                "corp_code": (el.findtext("corp_code") or "").strip(),
                "corp_name": (el.findtext("corp_name") or "").strip(),
                "stock_code": (el.findtext("stock_code") or "").strip(),
            }
        )
    os.makedirs(os.path.dirname(_CORPCODE_CACHE), exist_ok=True)
    with open(_CORPCODE_CACHE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    return rows


def find_corp_code(name: str, *, listed_only: bool = True) -> list[dict]:
    """회사명으로 corp_code 후보를 찾는다 (부분 일치)."""
    rows = download_corp_codes()
    name = name.strip()
    hits = [r for r in rows if name in r["corp_name"]]
    if listed_only:
        # 상장사(종목코드 있음) 우선
        listed = [r for r in hits if r["stock_code"]]
        if listed:
            return listed
    return hits


# 보고서 코드: 사업보고서=11011, 반기=11012, 1분기=11013, 3분기=11014
def fetch_financials(corp_code: str, bsns_year: str, reprt_code: str = "11011") -> list[dict]:
    """단일회사 주요계정 재무제표(fnlttSinglAcnt). 매출·영업이익·순이익 등 정형 수치.

    각 행은 당기/전기/전전기 금액과 연결(CFS)/별도(OFS) 구분을 포함한다.
    데이터 없음(013)이면 빈 리스트.
    """
    params = {
        "crtfc_key": _api_key(),
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
    }
    with httpx.Client(timeout=20) as client:
        resp = client.get(f"{BASE}/fnlttSinglAcnt.json", params=params)
        resp.raise_for_status()
        data = resp.json()
    status = data.get("status")
    if status == "013":  # 데이터 없음
        return []
    if status != "000":
        raise DartError(f"DART 재무제표 오류: {status} {data.get('message')}")
    return data.get("list", [])


def recent_financials(corp_code: str, *, years: list[int] | None = None) -> tuple[str | None, list[dict]]:
    """최근 사업보고서 주요계정을 찾는다. 최신 연도부터 내려가며 데이터 있는 첫 해를 반환.

    사업보고서는 당기/전기/전전기를 함께 주므로 한 번 호출로 3개년 비교가 가능하다.
    """
    import datetime

    cur = datetime.date.today().year
    for y in (years or [cur, cur - 1, cur - 2]):
        rows = fetch_financials(corp_code, str(y), "11011")
        if rows:
            return str(y), rows
    return None, []


def fetch_document_text(rcept_no: str) -> str:
    """접수번호로 공시 원문을 받아 평문으로 변환."""
    if not rcept_no or not rcept_no.isdigit():
        raise DartError(f"잘못된 접수번호: {rcept_no!r}")

    params = {"crtfc_key": _api_key(), "rcept_no": rcept_no}
    with httpx.Client(timeout=60) as client:
        resp = client.get(f"{BASE}/document.xml", params=params)
        resp.raise_for_status()
        content = resp.content

    # 응답이 ZIP 이 아니면 보통 에러 JSON/XML
    if not content[:2] == b"PK":
        raise DartError(f"문서 다운로드 실패(ZIP 아님): {content[:200]!r}")

    texts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in zf.namelist():
            raw = zf.read(name)
            texts.append(_decode(raw))
    return _strip_dart_xml("\n".join(texts))


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _strip_dart_xml(xml: str) -> str:
    """DART XML 태그 제거 후 본문만 남긴다."""
    # 스크립트/스타일 류 제거
    xml = re.sub(r"<(USERMARK|IMAGE)[^>]*>.*?</\1>", " ", xml, flags=re.S | re.I)
    # 태그를 공백/개행으로
    xml = re.sub(r"</(TABLE|TR|P|TITLE|SECTION-\d|ARTICLE)>", "\n", xml, flags=re.I)
    xml = re.sub(r"<[^>]+>", " ", xml)
    # HTML 엔티티 정리
    xml = (
        xml.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    xml = re.sub(r"[ \t]+", " ", xml)
    xml = re.sub(r"\n\s*\n+", "\n\n", xml)
    return xml.strip()
