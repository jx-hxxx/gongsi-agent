"""사전요약 백필 — 이미 적재된 코퍼스(corpus_<corp_code>)에서 공시별 요약을 생성.

청크는 그대로 두고(재임베딩 없음), 각 공시 원문을 DART 에서 다시 받아
3-트랙 규칙으로 요약해 summary_<corp_code> 에 저장한다.

사용:
    python build_summaries.py --company 삼성전자 --rcept 20250311001085   # 1건만(빠른 테스트)
    python build_summaries.py --company 삼성전자                          # 그 회사 전체
    python build_summaries.py                                            # 기본 회사 전체
"""
from __future__ import annotations

import argparse

from app.data import dart
from app.rag.vectorstore import get_vector_store, summary_collection
from app.services import summarize

DEFAULT_COMPANIES = ["삼성전자", "현대자동차"]


def corpus_collection(corp_code: str) -> str:
    return f"corpus_{corp_code}"


def run(companies: list[str], only_rcept: str | None, limit: int | None) -> None:
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
        if limit:
            pairs = pairs[:limit]
        print(f"\n=== {name} ({corp}) — 대상 공시 {len(pairs)}건 → summary_{corp} ===")

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
    args = ap.parse_args()
    companies = [args.company] if args.company else DEFAULT_COMPANIES
    run(companies, args.rcept, args.limit)
