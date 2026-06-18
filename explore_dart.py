"""OpenDART 공시 도메인 탐색기.

한 기업의 공시가 (1) 연간 몇 건인지, (2) 유형이 어떻게 나뉘는지,
(3) 문서가 몇 쪽/몇 글자인지, (4) 어떤 구조인지를 한눈에 보여준다.

사용:
    python explore_dart.py 삼성전자 2025
"""
from __future__ import annotations

import re
import sys

from app.data import dart

# OpenDART 공시유형 코드(pblntf_ty)
PBLNTF_TYPES = {
    "A": "정기공시",
    "B": "주요사항보고",
    "C": "발행공시",
    "D": "지분공시",
    "E": "기타공시",
    "F": "외부감사관련",
    "G": "펀드공시",
    "H": "자산유동화",
    "I": "거래소공시",
    "J": "공정위공시",
}

# 개념상 4대 분류로 롤업
ROLLUP = {
    "A": "정기공시", "F": "정기공시",
    "B": "수시공시", "C": "수시공시", "I": "수시/공정공시",
    "D": "지분공시",
    "E": "기타", "G": "기타", "H": "기타", "J": "기타",
}


def explore(company: str, year: int) -> None:
    bgn, end = f"{year}0101", f"{year}1231"

    # 1) corp_code 찾기
    print(f"🔎 '{company}' corp_code 조회 중... (corpCode 첫 실행 시 다운로드)")
    cands = dart.find_corp_code(company)
    if not cands:
        print(f"❌ '{company}' 를 찾지 못함.")
        return
    target = cands[0]
    print(f"   → {target['corp_name']} (corp_code={target['corp_code']}, 종목={target['stock_code'] or '비상장'})")
    if len(cands) > 1:
        print(f"   (동명/유사 {len(cands)}건 중 첫 번째 사용)")
    corp = target["corp_code"]

    # 2) 연간 전체 공시 건수
    total = dart.list_disclosures(corp_code=corp, bgn_de=bgn, end_de=end, page_count=1)
    total_count = int(total.get("total_count", 0))
    print(f"\n📊 {company} {year}년 공시: 총 {total_count}건")

    # 3) 유형별 건수 (pblntf_ty A~J)
    print("\n[공시유형별 건수]")
    rollup_counts: dict[str, int] = {}
    for code, label in PBLNTF_TYPES.items():
        r = dart.list_disclosures(corp_code=corp, bgn_de=bgn, end_de=end, pblntf_ty=code, page_count=1)
        cnt = int(r.get("total_count", 0))
        if cnt:
            print(f"  {code} {label:<10} {cnt:>4}건   → {ROLLUP[code]}")
            rollup_counts[ROLLUP[code]] = rollup_counts.get(ROLLUP[code], 0) + cnt

    print("\n[개념 4대 분류 롤업]")
    for k, v in sorted(rollup_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<14} {v:>4}건")

    # 4) 최근 공시 제목 샘플
    recent = dart.list_disclosures(corp_code=corp, bgn_de=bgn, end_de=end, page_count=15)
    rows = recent.get("list", [])
    print(f"\n[최근 공시 {len(rows)}건 제목]")
    for r in rows:
        print(f"  {r['rcept_dt']}  {r['report_nm'].strip()}  (rcept_no={r['rcept_no']})")

    # 5) 샘플 문서 1건 분량/구조
    if rows:
        sample = rows[0]
        print(f"\n📄 샘플 문서 분석: {sample['report_nm'].strip()} (rcept_no={sample['rcept_no']})")
        try:
            text = dart.fetch_document_text(sample["rcept_no"])
            chars = len(text)
            print(f"   글자수: {chars:,}자  /  대략 {max(1, chars // 1200)}쪽 (1200자/쪽 기준)")
            # 섹션 머리표 추출
            sections = []
            for line in text.split("\n"):
                s = line.strip()
                if s and len(s) < 50 and re.match(r"^(제?\s*\d+\s*[조항]|[0-9]+[\.\)]|[ⅠⅡⅢⅣ]+\.|[가-힣]\.)", s):
                    sections.append(s)
            if sections:
                print(f"   섹션 머리표({len(sections)}개) 예시:")
                for s in sections[:10]:
                    print(f"     - {s}")
            print("\n   본문 미리보기(앞 400자):")
            print("   " + text[:400].replace("\n", " "))
        except Exception as e:
            print(f"   (문서 다운로드 실패: {e})")


if __name__ == "__main__":
    company = sys.argv[1] if len(sys.argv) > 1 else "삼성전자"
    year = int(sys.argv[2]) if len(sys.argv) > 2 else 2025
    explore(company, year)
