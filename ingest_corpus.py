"""공시 코퍼스 적재 — 선별 기준에 따라 여러 기업 공시를 Chroma에 미리 저장.

선별 기준 (노션 "공시 코퍼스 적재 선별 기준" 과 동일):
  포함 유형: A(정기) B(주요사항) C(발행) I(거래소)
  지분공시 D: 보고서명에 '대량보유' 또는 '최대주주' 포함만
  E/F/G/H/J: 제외

사용:
    python ingest_corpus.py --dry-run            # 목록만 집계(빠름)
    python ingest_corpus.py                       # 실제 임베딩 적재(오래 걸림)
    python ingest_corpus.py --bgn 20230616 --end 20260616
"""
from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.data import dart
from app.rag.chunker import split_into_chunks
from app.rag.vectorstore import get_vector_store

COMPANIES = ["삼성전자", "현대자동차"]
# 처리 순서: 작은 공시 먼저 → 거대 정기공시(A)는 마지막 (진행률이 빨리 보임)
# F(외부감사) 포함 — 감사의견 거절·한정 같은 중대사항 누락 방지
INCLUDE_TYPES = ["I", "B", "C", "F", "A"]
D_INCLUDE = ("대량보유", "최대주주")     # 지분공시 중 의미있는 것만


def list_all(corp: str, bgn: str, end: str, pblntf_ty: str) -> list[dict]:
    """페이지네이션 포함 전체 목록."""
    out: list[dict] = []
    page = 1
    while True:
        d = dart.list_disclosures(
            corp_code=corp, bgn_de=bgn, end_de=end,
            pblntf_ty=pblntf_ty, page_no=page, page_count=100,
        )
        out += d.get("list", [])
        total_page = int(d.get("total_page", 1) or 1)
        if page >= total_page:
            break
        page += 1
    return out


def collect_targets(corp: str, bgn: str, end: str) -> list[dict]:
    """선별 기준 통과 공시만 추린다 (rcept_no 중복 제거)."""
    seen: set[str] = set()
    targets: list[dict] = []
    for code in INCLUDE_TYPES:
        for r in list_all(corp, bgn, end, code):
            if r["rcept_no"] not in seen:
                seen.add(r["rcept_no"])
                r["_ty"] = code
                targets.append(r)
    for r in list_all(corp, bgn, end, "D"):
        if r["rcept_no"] in seen:
            continue
        if any(k in r["report_nm"] for k in D_INCLUDE):
            seen.add(r["rcept_no"])
            r["_ty"] = "D"
            targets.append(r)
    return targets


def corpus_collection(corp_code: str) -> str:
    return f"corpus_{corp_code}"


def run(bgn: str, end: str, dry_run: bool) -> None:
    settings = get_settings()
    store = None if dry_run else get_vector_store()

    for name in COMPANIES:
        cands = dart.find_corp_code(name)
        if not cands:
            print(f"❌ {name} corp_code 없음")
            continue
        corp = cands[0]["corp_code"]
        coll = corpus_collection(corp)
        targets = collect_targets(corp, bgn, end)

        # 유형별 집계
        by_ty: dict[str, int] = {}
        for t in targets:
            by_ty[t["_ty"]] = by_ty.get(t["_ty"], 0) + 1
        print(f"\n=== {name} ({corp}) {bgn}~{end} ===")
        print(f"  선별 통과: {len(targets)}건  유형별={by_ty}")

        if dry_run:
            for t in targets[:8]:
                print(f"    [{t['_ty']}] {t['rcept_dt']} {t['report_nm'].strip()}")
            if len(targets) > 8:
                print(f"    ... 외 {len(targets) - 8}건")
            continue

        # 실제 적재
        done = skipped = total_chunks = 0
        for i, t in enumerate(targets, 1):
            rcept = t["rcept_no"]
            if store.has_disclosure(coll, rcept):
                skipped += 1
                continue
            try:
                text = dart.fetch_document_text(rcept)
                chunks = split_into_chunks(
                    text, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap
                )
                base_meta = {
                    "corp_name": name,
                    "pblntf_ty": t["_ty"],
                    "report_nm": t["report_nm"].strip(),
                    "rcept_dt": t["rcept_dt"],
                }
                n = store.index_corpus_disclosure(coll, rcept, chunks, base_meta)
                total_chunks += n
                done += 1
                print(f"  [{i}/{len(targets)}] {t['report_nm'].strip()[:30]} → {n}청크 (누적 {total_chunks})")
            except Exception as e:
                print(f"  [{i}/{len(targets)}] 실패 {rcept}: {e}")
        print(f"  ✅ {name}: 신규 {done}건 / 스킵 {skipped}건 / 총 {total_chunks}청크 (collection={coll})")

    if not dry_run:
        print("\n적재 완료.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bgn", default="20230616")
    ap.add_argument("--end", default="20260616")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.bgn, args.end, args.dry_run)
