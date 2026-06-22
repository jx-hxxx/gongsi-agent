"""사전요약 백필 — 이미 적재된 코퍼스(corpus_<corp_code>)에서 공시별 요약을 생성.

청크는 그대로 두고(재임베딩 없음), 각 공시 원문을 DART 에서 다시 받아
3-트랙 규칙으로 요약해 summary_<corp_code> 에 저장한다.

기본 동작: 정기보고서(사업·반기·분기·감사보고서)만 사전요약한다.
  - 정기보고서는 정보가 풍부해 사전요약의 가치가 크고, 그 외 단발성·정량 위주 공시는
    질문 시 실시간 RAG 폴백으로 충분하다(요약 빌드 비용을 정기보고서에 집중).
  - 모든 공시를 빌드하려면 --all.

사용:
    python build_summaries.py --company 삼성전자 --rcept 20250311001085   # 1건만(빠른 테스트)
    python build_summaries.py --company 삼성전자                          # 그 회사 정기보고서
    python build_summaries.py                                            # 기본 회사 정기보고서
    python build_summaries.py --all                                      # 정기보고서 외 전부 포함
    python build_summaries.py --dry-run                                  # 대상만 출력(빌드 안 함)
"""
from __future__ import annotations

import argparse

from app.data import dart
from app.rag.vectorstore import get_vector_store, summary_collection
from app.services import summarize

DEFAULT_COMPANIES = ["삼성전자", "현대자동차"]

# 사전요약 대상이 되는 정기보고서 종류 (report_nm 부분일치, [기재정정] 등 접두 포함)
PERIODIC_KEYWORDS = ("사업보고서", "반기보고서", "분기보고서", "감사보고서")


def corpus_collection(corp_code: str) -> str:
    return f"corpus_{corp_code}"


def is_periodic(report_nm: str | None) -> bool:
    """정기보고서(사업·반기·분기·감사보고서)인지 report_nm 부분일치로 판정."""
    rn = report_nm or ""
    return any(k in rn for k in PERIODIC_KEYWORDS)


def run(
    companies: list[str],
    only_rcept: str | None,
    limit: int | None,
    include_all: bool = False,
    dry_run: bool = False,
) -> None:
    store = get_vector_store()
    for name in companies:
        cands = dart.find_corp_code(name)
        if not cands:
            print(f"❌ {name} corp_code 없음")
            continue
        corp = cands[0]["corp_code"]
        coll = corpus_collection(corp)
        pairs = store.distinct_rcept_nos(coll)
        if only_rcept:
            pairs = [(rc, m) for rc, m in pairs if rc == only_rcept]
        elif not include_all:
            # 기본: 정기보고서만 (그 외는 실시간 RAG 폴백에 맡김)
            pairs = [(rc, m) for rc, m in pairs if is_periodic((m or {}).get("report_nm"))]
        if limit:
            pairs = pairs[:limit]
        scope = "전체" if include_all or only_rcept else "정기보고서"
        print(f"\n=== {name} ({corp}) — 대상 공시 {len(pairs)}건 [{scope}] → summary_{corp} ===")

        if dry_run:
            for j, (rcept, meta) in enumerate(pairs, 1):
                print(f"  [{j}] {((meta or {}).get('report_nm') or '')[:40]} ({rcept})")
            print(f"  (dry-run) 빌드하지 않음. 대상 {len(pairs)}건.")
            continue

        done = total = 0
        for j, (rcept, meta) in enumerate(pairs, 1):
            report_nm = (meta or {}).get("report_nm") or ""
            rcept_dt = (meta or {}).get("rcept_dt") or ""
            try:
                text = dart.fetch_document_text(rcept)
                ns = summarize.build_and_store(corp, name, rcept, report_nm, rcept_dt, text)
                total += ns
                done += 1
                print(f"  [{j}/{len(pairs)}] {report_nm[:28]} ({rcept}) → 요약 {ns}개 (누적 {total})")
            except Exception as e:
                print(f"  [{j}/{len(pairs)}] 실패 {rcept}: {type(e).__name__}: {e}")
        print(f"  ✅ {name}: {done}건 처리 / 요약 {total}개 (collection=summary_{corp})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", help="회사명(미지정 시 기본 회사 전체)")
    ap.add_argument("--rcept", help="특정 접수번호 1건만")
    ap.add_argument("--limit", type=int, help="앞에서 N건만")
    ap.add_argument("--all", action="store_true", help="정기보고서 외 모든 공시 포함")
    ap.add_argument("--dry-run", action="store_true", help="대상만 출력하고 빌드하지 않음")
    args = ap.parse_args()
    companies = [args.company] if args.company else DEFAULT_COMPANIES
    run(companies, args.rcept, args.limit, include_all=args.all, dry_run=args.dry_run)
