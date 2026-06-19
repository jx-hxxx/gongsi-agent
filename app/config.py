"""애플리케이션 설정. .env 에서 로드한다."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM (OpenAI) ---
    openai_api_key: str = ""
    router_model: str = "gpt-5.1"       # 의도/스코프 분류 (가벼움)
    summary_model: str = "gpt-5.1"
    qa_model: str = "gpt-5.1"
    macro_model: str = "gpt-5.1"        # ECOS 거시 결합 에이전트
    verification_model: str = "o4-mini"  # 추론 특화 → 검증

    # --- 데이터 소스 ---
    dart_api_key: str = ""
    ecos_api_key: str = "sample"

    # --- 임베딩 ---
    # provider: "local"(BGE-m3) 또는 "openai"(text-embedding-3-small)
    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "cpu"

    # --- 저장 ---
    chroma_dir: str = "./data/chroma"
    sqlite_path: str = "./data/app.db"

    # --- 검증 임계값 (grounded_score → verdict) ---
    verify_pass_min: float = 0.7      # 이상이면 pass
    verify_partial_min: float = 0.4   # 이상이면 partial, 미만이면 fail(→ CRAG 재검색)

    # --- RAG 파라미터 ---
    chunk_size: int = 800          # 청크당 대략 글자 수
    chunk_overlap: int = 120       # 청크 간 겹침
    top_k: int = 5                 # 검색 시 가져올 근거 문단 수
    rerank_enabled: bool = True    # 도메인 가점/노이즈 감점 rerank 사용
    candidate_k: int = 20          # rerank 전 후보 검색 개수
    keyword_fallback_enabled: bool = True  # 정량 표 질의 시 본문 직접 스캔(재현율 보강)

    # OpenAI 모델은 litellm 이 모델명만으로 라우팅한다 (prefix 불필요).
    @property
    def litellm_router_model(self) -> str:
        return self.router_model

    @property
    def litellm_summary_model(self) -> str:
        return self.summary_model

    @property
    def litellm_qa_model(self) -> str:
        return self.qa_model

    @property
    def litellm_macro_model(self) -> str:
        return self.macro_model

    @property
    def litellm_verification_model(self) -> str:
        return self.verification_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
