"""청킹 파라미터 실험용 적재 — 1개년치를 chunk_size별 컬렉션에 적재.

목적: 400 / 800 / 1200 중 어떤 chunk_size가 검색이 잘 되는지 비교하기 위한
      별도 코퍼스를 만든다. (기존 운영 코퍼스 corpus_<corp_code> 는 절대 건드리지 않음)

비율 유지 규칙: overlap = round(chunk_size * 0.15)
      → 400/60, 800/120, 1200/180  (청크 대비 겹침 비율을 동일하게 맞춰 공정 비교)

임베딩: OpenAI text-embedding-3-small (.env 의 EMBEDDING_PROVIDER=openai 필수)
      적재와 검색은 반드시 같은 임베딩 모델을 써야 한다.

실행 (프로젝트 루트에서):
    python eval/ingest_eval.py                          # 삼성전자 1년치, 400/800/1200 전부
    python eval/ingest_eval.py --company 현대자동차
    python eval/ingest_eval.py --sizes 400 800
    python eval/ingest_eval.py --bgn 20250616 --end 20260616
"""
import argparse
import os
import sys

# eval/ 하위에서도 app / ingest_corpus 를 import 할 수 있게 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.data import dart
from app.rag.chunker import split_into_chunks
from app.rag.vectorstore import get_vector_store
from ingest_corpus import collect_targets  # 운영 적재와 동일한 선별 기준 재사용

RATIO = 0.15  # overlap 비율


def eval_collection(size: int) -> str:
    return f"corpus_eval_{size}"


def run(company: str, bgn: str, end: str, sizes: list[int]) -> None:
    settings = get_settings()
    if settings.embedding_provider != "openai":
        print(
            f"⚠️  EMBEDDING_PROVIDER 가 'openai' 가 아닙니다 (현재: {settings.embedding_provider}). "
            ".env 를 확인하세요. 적재/검색 임베딩이 달라지면 결과가 무의미합니다."
        )
        return

    store = get_vector_store()
    cands = dart.find_corp_code(company)
    if not cands:
        print(f"❌ {company} corp_code 없음")
        return
    corp = cands[0]["corp_code"]
    targets = collect_targets(corp, bgn, end)

    by_ty: dict[str, int] = {}
    for t in targets:
        by_ty[t["_ty"]] = by_ty.get(t["_ty"], 0) + 1
    print(f"=== {company} ({corp}) {bgn}~{end} : 선별 {len(targets)}건  유형별={by_ty} ===")
    print(f"    chunk_size={sizes} / overlap=" + ", ".join(f"{s}:{round(s*RATIO)}" for s in sizes))

    # 컬렉션 초기화 (재실행 시 중복 적재 방지)
    for size in sizes:
        try:
            store.client.delete_collection(eval_collection(size))
        except Exception:
            pass

    totals = {s: 0 for s in sizes}
    for i, t in enumerate(targets, 1):
        rcept = t["rcept_no"]
        try:
            text = dart.fetch_document_text(rcept)  # 1회만 받아서 3가지로 청킹
        except Exception as e:
            print(f"  [{i}/{len(targets)}] fetch 실패 {rcept}: {e}")
            continue
        base_meta = {
            "corp_name": company,
            "pblntf_ty": t["_ty"],
            "report_nm": t["report_nm"].strip(),
            "rcept_dt": t["rcept_dt"],
        }
        for size in sizes:
            overlap = round(size * RATIO)
            chunks = split_into_chunks(text, chunk_size=size, overlap=overlap)
            n = store.index_corpus_disclosure(eval_collection(size), rcept, chunks, base_meta)
            totals[size] += n
        print(f"  [{i}/{len(targets)}] {t['report_nm'].strip()[:24]} ✓")

    print("\n적재 완료:")
    for size in sizes:
        print(
            f"  chunk_size={size:>4} (overlap={round(size*RATIO)}) "
            f"→ {totals[size]:>6}청크   collection={eval_collection(size)}"
        )
    print("\n다음: python eval/eval_chunking.py")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", default="삼성전자")
    ap.add_argument("--bgn", default="20250616", help="시작일 YYYYMMDD (기본=최근 1년)")
    ap.add_argument("--end", default="20260616", help="종료일 YYYYMMDD")
    ap.add_argument("--sizes", nargs="+", type=int, default=[400, 800, 1200])
    args = ap.parse_args()
    run(args.company, args.bgn, args.end, args.sizes)
