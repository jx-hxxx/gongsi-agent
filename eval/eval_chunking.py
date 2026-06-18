"""청킹 파라미터 평가 — 골든셋으로 컬렉션별 Hit@k / MRR 비교.

지표:
  Hit@k     : 정답 근거(expected_phrase)가 top_k 안에 잡힌 질문 비율  ← 주력 지표
  MRR       : 정답이 처음 나온 순위의 역수 평균 (1.0 = 항상 1등으로 잡음)
  평균top1유사도 : top1 결과의 유사도 평균 (참고용)

왜 Hit@k 가 주력인가:
  chunk_size 가 검색에 미치는 영향을 '직접' 측정하는 지표라서.
  (LLM 답변 품질이 섞이지 않음 → 청크 효과만 깔끔하게 비교 가능)

실행 (프로젝트 루트에서, ingest_eval.py 먼저 돌린 뒤):
    python eval/eval_chunking.py
    python eval/eval_chunking.py --top-k 5 --sizes 400 800 1200
    python eval/eval_chunking.py --golden eval/golden_set.json
"""
import argparse
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.rag.vectorstore import get_vector_store

RATIO = 0.15


def norm(s: str) -> str:
    # 한글 정규화(NFC) + 공백/대소문자 무시 → phrase 매칭을 안정적으로
    return unicodedata.normalize("NFC", s).replace(" ", "").lower()


def eval_collection(size: int) -> str:
    return f"corpus_eval_{size}"


def search(store, coll_name: str, query: str, k: int):
    try:
        coll = store.client.get_collection(coll_name)
    except Exception:
        return None  # 컬렉션 없음
    q = store.embedder.embed_query(query)
    res = coll.query(query_embeddings=[q], n_results=k)
    docs = res.get("documents", [[]])[0]
    dists = res.get("distances", [[]])[0]
    return list(zip(docs, dists))


def hit_rank(results, expected_phrase: str) -> int:
    """정답 phrase 가 처음 등장한 순위(1-base). 없으면 0."""
    p = norm(expected_phrase)
    for idx, (doc, _) in enumerate(results, 1):
        if p in norm(doc):
            return idx
    return 0


def run(golden_path: str, sizes: list[int], k: int) -> None:
    store = get_vector_store()
    with open(golden_path, encoding="utf-8") as f:
        golden = json.load(f)
    # 템플릿 예시(작성 안내용) 항목은 제외
    golden = [g for g in golden if not g.get("_example")]
    if not golden:
        print("❌ golden_set.json 에 평가할 문항이 없습니다. (예시를 지우고 실제 문항을 채우세요)")
        return
    print(f"골든셋 {len(golden)}문항, top_k={k}\n")

    rows = []
    for size in sizes:
        coll = eval_collection(size)
        if search(store, coll, "테스트", 1) is None:
            print(f"⚠️  컬렉션 없음: {coll}  (먼저 python eval/ingest_eval.py 실행)")
            continue
        hits = 0
        mrr_sum = 0.0
        top1_sum = 0.0
        misses = []
        for item in golden:
            results = search(store, coll, item["question"], k)
            r = hit_rank(results, item["expected_phrase"])
            if r:
                hits += 1
                mrr_sum += 1.0 / r
            else:
                misses.append(str(item.get("id", "?")))
            if results:
                top1_sum += 1 - float(results[0][1])
        n = len(golden)
        rows.append((size, hits / n, mrr_sum / n, top1_sum / n, misses))
        print(
            f"chunk_size={size:>4} (overlap={round(size*RATIO)}): "
            f"Hit@{k}={hits}/{n}={hits/n:.0%}   MRR={mrr_sum/n:.3f}   "
            f"평균top1유사도={top1_sum/n:.3f}"
        )
        if misses:
            print(f"        놓친 문항 id: {', '.join(misses)}")

    if rows:
        best = max(rows, key=lambda x: (x[1], x[2]))  # Hit@k 우선, 동률이면 MRR
        print(
            f"\n👉 추천: chunk_size={best[0]} "
            f"(Hit@{k}={best[1]:.0%}, MRR={best[2]:.3f})"
        )
        print("   ※ Hit@k 동률이면 MRR 높은 쪽. 최종 확정은 verification 결과까지 같이 보고 팀 합의.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--golden", default=os.path.join(os.path.dirname(__file__), "golden_set.json")
    )
    ap.add_argument("--sizes", nargs="+", type=int, default=[400, 800, 1200])
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()
    run(args.golden, args.sizes, args.top_k)
