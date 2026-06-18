"""A단계 검증 (Claude 키 불필요): 데이터 + RAG 파이프라인이 실제로 도는지 확인.

실제 공시 → DART 다운로드 → 청킹 → BGE-m3 임베딩 → Chroma 저장 → 검색 테스트.

사용:
    python verify_rag.py 20251030000502
    python verify_rag.py 20251030000502 "자기주식 처분 수량은?"
"""
from __future__ import annotations

import sys

from app.config import get_settings
from app.data import dart
from app.rag.chunker import split_into_chunks
from app.rag.vectorstore import get_vector_store


def main(rcept_no: str, queries: list[str]) -> None:
    s = get_settings()
    analysis_id = f"verify_{rcept_no}"

    print(f"① DART 원문 다운로드 (rcept_no={rcept_no}) ...")
    text = dart.fetch_document_text(rcept_no)
    print(f"   ✓ {len(text):,}자 수신")

    print("② 청킹 ...")
    chunks = split_into_chunks(text, chunk_size=s.chunk_size, overlap=s.chunk_overlap)
    print(f"   ✓ {len(chunks)}개 청크 (chunk_size={s.chunk_size})")
    if chunks:
        print(f"   예시 청크[0] 섹션={chunks[0].section_title!r}")
        print(f"           본문: {chunks[0].text[:80].strip()}...")

    print("③ BGE-m3 임베딩 + Chroma 저장 (첫 실행 시 모델 ~2GB 다운로드, 오래 걸림) ...")
    store = get_vector_store()
    n = store.index_chunks(analysis_id, chunks)
    print(f"   ✓ {n}개 청크 임베딩·저장 완료")

    print("④ 검색 테스트 ...")
    for q in queries:
        print(f"\n   질문: {q}")
        hits = store.search(analysis_id, q, top_k=3)
        if not hits:
            print("     (검색 결과 없음)")
        for h in hits:
            print(f"     [score={h.score}] ({h.section_title or '-'}) {h.quote[:90].strip()}...")

    print("\n✅ A단계 통과: DART→청킹→임베딩→Chroma→검색 정상 동작")


if __name__ == "__main__":
    rcept_no = sys.argv[1] if len(sys.argv) > 1 else "20251030000502"
    queries = sys.argv[2:] if len(sys.argv) > 2 else [
        "자기주식 처분 수량과 금액은?",
        "처분 목적은 무엇인가?",
        "처분 대상 주식의 종류는?",
    ]
    main(rcept_no, queries)
