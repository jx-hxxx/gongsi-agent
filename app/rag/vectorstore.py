"""Chroma 기반 Vector DB.

공시 1건 = 1 collection. 분석 단위로 격리해 검색이 다른 공시와 섞이지 않게 한다.
검색 결과는 그대로 Citation(출처)으로 변환된다.
"""
from __future__ import annotations

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import get_settings
from app.rag.chunker import Chunk
from app.rag.embedder import get_embedder
from app.rag.rerank import keyword_terms, rerank_rows
from app.rag.rerank import _norm as _kw_norm
from app.schemas.disclosure import Citation


def _collection_name(analysis_id: str) -> str:
    # Chroma collection 이름 규칙(영숫자/_/-) 에 맞게
    return f"disc_{analysis_id}".replace("-", "_")[:60]


class VectorStore:
    def __init__(self):
        settings = get_settings()
        self.client = chromadb.PersistentClient(
            path=settings.chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
        )
        self.embedder = get_embedder()
        # keyword fallback 용 본문 캐시: {collection_name: (count, [(id, doc, meta), ...])}
        self._doc_cache: dict[str, tuple[int, list]] = {}

    def index_chunks(self, analysis_id: str, chunks: list[Chunk]) -> int:
        """청크를 임베딩해 저장한다. 저장된 청크 수를 반환."""
        if not chunks:
            return 0
        name = _collection_name(analysis_id)
        # 재실행 시 중복 방지
        try:
            self.client.delete_collection(name)
        except Exception:
            pass
        collection = self.client.create_collection(name, metadata={"analysis_id": analysis_id})

        embeddings = self.embedder.embed_documents([c.text for c in chunks])
        collection.add(
            ids=[c.chunk_id for c in chunks],
            documents=[c.text for c in chunks],
            embeddings=embeddings,
            metadatas=[
                {"section_title": c.section_title or "", "order": c.order} for c in chunks
            ],
        )
        return len(chunks)

    def search(self, analysis_id: str, query: str, top_k: int | None = None) -> list[Citation]:
        """질문과 가장 가까운 문단을 Citation 형태로 반환."""
        settings = get_settings()
        k = top_k or settings.top_k
        name = _collection_name(analysis_id)
        try:
            collection = self.client.get_collection(name)
        except Exception:
            return []

        q_emb = self.embedder.embed_query(query)
        res = collection.query(query_embeddings=[q_emb], n_results=k)

        citations: list[Citation] = []
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for cid, doc, meta, dist in zip(ids, docs, metas, dists):
            citations.append(
                Citation(
                    chunk_id=cid,
                    section_title=(meta or {}).get("section_title") or None,
                    quote=doc,
                    # cosine distance -> 유사도 근사 (0~1)
                    score=round(1 - float(dist), 4) if dist is not None else None,
                )
            )
        return citations

    def delete(self, analysis_id: str) -> None:
        try:
            self.client.delete_collection(_collection_name(analysis_id))
        except Exception:
            pass

    # ===== 코퍼스 적재 (여러 공시를 한 컬렉션에) =====
    def index_corpus_disclosure(
        self, collection_name: str, rcept_no: str, chunks: list[Chunk], base_meta: dict
    ) -> int:
        """공시 1건을 코퍼스 컬렉션에 추가(append). 청크 id 는 rcept_no 로 네임스페이스."""
        if not chunks:
            return 0
        coll = self.client.get_or_create_collection(collection_name)
        embeddings = self.embedder.embed_documents([c.text for c in chunks])
        ids = [f"{rcept_no}-{c.chunk_id}" for c in chunks]
        docs = [c.text for c in chunks]
        metas = [
            {
                **base_meta,
                "rcept_no": rcept_no,
                "section_title": c.section_title or "",
                "order": c.order,
            }
            for c in chunks
        ]
        # Chroma 1회 add 한도(약 5461) 초과 방지 — 사업보고서 등 대형 문서는 나눠 저장
        B = 2000
        for i in range(0, len(ids), B):
            coll.add(
                ids=ids[i : i + B],
                documents=docs[i : i + B],
                embeddings=embeddings[i : i + B],
                metadatas=metas[i : i + B],
            )
        return len(chunks)

    def _all_docs(self, collection_name: str, coll) -> list:
        """컬렉션 전체 (id, doc, meta) 를 메모리 캐시로 보관(count 바뀌면 갱신)."""
        cnt = coll.count()
        cached = self._doc_cache.get(collection_name)
        if cached and cached[0] == cnt:
            return cached[1]
        got = coll.get(include=["documents", "metadatas"])
        rows = list(zip(got.get("ids", []), got.get("documents", []), got.get("metadatas", [])))
        self._doc_cache[collection_name] = (cnt, rows)
        return rows

    def _keyword_scan(self, collection_name: str, coll, query: str) -> list:
        """정량 표 도메인 구를 본문에서 직접 스캔(정규화 substring). 매칭 (id, doc, meta) 반환."""
        terms = keyword_terms(query)
        if not terms:
            return []
        nterms = [_kw_norm(t) for t in terms]
        hits = []
        for cid, doc, meta in self._all_docs(collection_name, coll):
            dn = _kw_norm(doc or "")
            if any(t in dn for t in nterms):
                hits.append((cid, doc or "", meta or {}))
        return hits

    def search_corpus(
        self,
        collection_name: str,
        query: str,
        top_k: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        prefer_recent: bool = False,
    ) -> list[Citation]:
        """코퍼스 컬렉션(corpus_<corp_code>)에서 근거 문단을 Citation 으로 검색.

        date_from / date_to: 접수일(rcept_dt, YYYYMMDD) 기간 한정. 메타데이터가 문자열로
            저장돼 있어 Chroma 숫자비교($gte)가 안 되므로 Python 에서 후필터한다
            (YYYYMMDD 는 사전순=날짜순이라 문자열 비교로 충분).
        prefer_recent: True 면 접수일 최신순으로 정렬해 상위 k개를 돌려준다.
        """
        settings = get_settings()
        k = top_k or settings.top_k
        try:
            coll = self.client.get_collection(collection_name)
        except Exception:
            return []

        # 후보를 넉넉히 뽑는다 (rerank·날짜필터·최신정렬에 쓸 풀).
        candidate_k = max(k, settings.candidate_k)
        n = max(candidate_k, k * 8) if (date_from or date_to or prefer_recent) else candidate_k
        q_emb = self.embedder.embed_query(query)
        res = coll.query(
            query_embeddings=[q_emb], n_results=n,
            include=["documents", "metadatas", "distances"],
        )

        # 후보 rows 구성 + 기간 후필터
        rows: list[dict] = []
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for cid, doc, meta, dist in zip(ids, docs, metas, dists):
            m = meta or {}
            dt = m.get("rcept_dt") or None
            if date_from and (dt or "") < date_from:
                continue
            if date_to and (dt or "99999999") > date_to:
                continue
            rows.append({
                "id": cid, "doc": doc or "", "meta": m,
                "distance": float(dist) if dist is not None else None,
                "score": (1 - float(dist)) if dist is not None else 0.0,
            })

        # keyword fallback: 정량 표 도메인 구를 본문에서 직접 스캔해 후보에 합류시킨다.
        # (벡터가 표 숫자를 못 끌어오는 재현율 누수 보강 — 일반 질문이면 terms 없어 no-op)
        if settings.keyword_fallback_enabled:
            existing = {r["id"] for r in rows}
            kw_score = 0.5  # 명목 base — 순위는 rerank 의 도메인 가점(0.46)이 결정
            for cid, doc, m in self._keyword_scan(collection_name, coll, query):
                if cid in existing:
                    continue
                dt = m.get("rcept_dt") or None
                if date_from and (dt or "") < date_from:
                    continue
                if date_to and (dt or "99999999") > date_to:
                    continue
                rows.append({
                    "id": cid, "doc": doc, "meta": m,
                    "distance": None, "score": kw_score,
                })
                existing.add(cid)

        # 정렬: 최신 우선이면 날짜 우선(rerank 점수 보조), 아니면 rerank(켜졌을 때) 또는 유사도
        if prefer_recent:
            if settings.rerank_enabled:
                rerank_rows(query, rows)
            rows.sort(
                key=lambda r: (r["meta"].get("rcept_dt") or "", r.get("rerank_score", r["score"])),
                reverse=True,
            )
        elif settings.rerank_enabled:
            rerank_rows(query, rows)  # rerank_score 내림차순 정렬됨
        else:
            rows.sort(key=lambda r: r["score"], reverse=True)

        citations: list[Citation] = []
        for r in rows[:k]:
            m = r["meta"]
            citations.append(
                Citation(
                    chunk_id=r["id"],
                    section_title=m.get("section_title") or None,
                    quote=r["doc"],
                    score=round(r["score"], 4) if r["distance"] is not None else None,
                    rcept_no=m.get("rcept_no") or None,
                    report_nm=m.get("report_nm") or None,
                    rcept_dt=m.get("rcept_dt") or None,
                )
            )
        return citations

    def corpus_count(self, collection_name: str) -> int:
        try:
            return self.client.get_collection(collection_name).count()
        except Exception:
            return 0

    def has_disclosure(self, collection_name: str, rcept_no: str) -> bool:
        """이미 적재된 공시인지(중복 방지)."""
        try:
            coll = self.client.get_collection(collection_name)
        except Exception:
            return False
        got = coll.get(where={"rcept_no": rcept_no}, limit=1)
        return bool(got.get("ids"))

    # ===== 사전요약 컬렉션 (3-트랙 요약: summary_<corp_code>) =====
    def index_summaries(self, collection_name: str, items: list[dict]) -> int:
        """섹션/전체 요약을 임베딩해 요약 컬렉션에 저장. items=[{id, text, metadata}]."""
        items = [it for it in items if (it.get("text") or "").strip()]
        if not items:
            return 0
        coll = self.client.get_or_create_collection(collection_name)
        embeddings = self.embedder.embed_documents([it["text"] for it in items])
        coll.add(
            ids=[it["id"] for it in items],
            documents=[it["text"] for it in items],
            embeddings=embeddings,
            metadatas=[it["metadata"] for it in items],
        )
        self._doc_cache.pop(collection_name, None)
        return len(items)

    def delete_summaries_for(self, collection_name: str, rcept_no: str) -> None:
        """재생성(idempotent) 전, 해당 공시의 기존 요약 제거."""
        try:
            coll = self.client.get_collection(collection_name)
        except Exception:
            return
        try:
            coll.delete(where={"rcept_no": rcept_no})
        except Exception:
            pass

    def search_summaries(
        self, collection_name: str, query: str, top_k: int | None = None
    ) -> list[Citation]:
        """요약 컬렉션에서 질문과 가까운 섹션요약을 Citation 으로 검색(꺼내기만 → 빠름)."""
        settings = get_settings()
        k = top_k or settings.summary_top_k
        try:
            coll = self.client.get_collection(collection_name)
        except Exception:
            return []
        q_emb = self.embedder.embed_query(query)
        res = coll.query(
            query_embeddings=[q_emb], n_results=k,
            include=["documents", "metadatas", "distances"],
        )
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        out: list[Citation] = []
        for cid, doc, meta, dist in zip(ids, docs, metas, dists):
            m = meta or {}
            out.append(
                Citation(
                    chunk_id=cid,
                    section_title=m.get("section_title") or None,
                    quote=doc,
                    score=round(1 - float(dist), 4) if dist is not None else None,
                    rcept_no=m.get("rcept_no") or None,
                    report_nm=m.get("report_nm") or None,
                    rcept_dt=m.get("rcept_dt") or None,
                )
            )
        return out

    def has_summaries(self, collection_name: str) -> bool:
        try:
            return self.client.get_collection(collection_name).count() > 0
        except Exception:
            return False

    def distinct_rcept_nos(self, collection_name: str) -> list[tuple[str, dict]]:
        """코퍼스 컬렉션에서 (rcept_no, 대표 메타) 목록을 추린다(백필 대상 산출용)."""
        try:
            coll = self.client.get_collection(collection_name)
        except Exception:
            return []
        got = coll.get(include=["metadatas"])
        seen: dict[str, dict] = {}
        for m in got.get("metadatas", []) or []:
            rc = (m or {}).get("rcept_no")
            if rc and rc not in seen:
                seen[rc] = m
        return list(seen.items())


def summary_collection(corp_code: str) -> str:
    """사전요약 컬렉션 이름 (corpus_<corp_code> 와 짝)."""
    return f"summary_{corp_code}"


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
