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

    # --- 3-트랙 요약 (사전요약 + RAG) ---
    small_disclosure_chars: int = 12000  # 이하면 '작은 공시' → 원문 통째 요약
    summary_top_k: int = 4               # 요약 트랙에서 가져올 섹션요약 개수
    min_section_chars: int = 200         # 사전요약 시 이보다 짧은 섹션은 스킵
    # 큰 공시: 잘게 쪼개진 섹션을 ~target_chars 묶음으로 합치고, 묶음 수를 max 로 제한
    # (사업보고서가 수백 개 micro-섹션으로 쪼개져 LLM 호출이 폭증하는 것 방지)
    summary_section_target_chars: int = 6000  # 묶음 1개 목표 길이
    summary_max_sections: int = 40            # 공시 1건당 섹션요약(=LLM 호출) 상한

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
