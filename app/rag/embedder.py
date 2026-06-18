"""임베딩 추상화 계층.

기본 구현은 로컬 BGE-m3(dense). 배포 환경에서 서버가 무겁다면
이 인터페이스만 구현한 다른 백엔드(OpenAI/Voyage API 등)로 교체하면 된다.
"""
from __future__ import annotations

from typing import Protocol

from app.config import get_settings


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class BGEM3Embedder:
    """로컬 BGE-m3 dense 임베딩 (sentence-transformers).

    무거운 모델 로드를 지연(lazy)시켜 import 시점 비용을 피한다.
    """

    def __init__(self, model_name: str | None = None, device: str | None = None):
        settings = get_settings()
        self.model_name = model_name or settings.embedding_model
        self.device = device or settings.embedding_device
        self._model = None

    @property
    def model(self):
        if self._model is None:
            # 무거운 import 도 지연
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self.model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        vec = self.model.encode(
            [text], normalize_embeddings=True, show_progress_bar=False
        )[0]
        return vec.tolist()


class OpenAIEmbedder:
    """OpenAI 임베딩 API (text-embedding-3-small 등). 대량 적재에 빠르다."""

    def __init__(self, model: str, api_key: str):
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(api_key=api_key)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):  # 100개씩 배치
            batch = texts[i : i + 100]
            resp = self.client.embeddings.create(model=self.model, input=batch)
            out.extend(d.embedding for d in resp.data)
        return out

    def embed_query(self, text: str) -> list[float]:
        resp = self.client.embeddings.create(model=self.model, input=[text])
        return resp.data[0].embedding


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """싱글턴 임베더. provider 설정에 따라 로컬(BGE-m3) 또는 OpenAI 선택."""
    global _embedder
    if _embedder is None:
        s = get_settings()
        if s.embedding_provider == "openai":
            _embedder = OpenAIEmbedder(s.embedding_model, s.openai_api_key)
        else:
            _embedder = BGEM3Embedder()
    return _embedder
