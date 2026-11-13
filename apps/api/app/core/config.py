"""Runtime configuration.

Everything is env-overridable with the ``NEXUS_`` prefix so the same image can run
in mock mode (no external services) or against a real model / vector store.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXUS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "NexusAgent"
    api_prefix: str = "/api"
    debug: bool = False

    data_dir: Path = APP_DIR / "data"
    database_url: str = f"sqlite:///{(APP_DIR / 'data' / 'nexus.db').as_posix()}"

    # --- LLM ---------------------------------------------------------------
    llm_provider: str = "mock"  # mock | openai
    llm_base_url: str = "https://api.siliconflow.cn/v1"
    llm_api_key: str = ""
    llm_model: str = "Qwen/Qwen2.5-7B-Instruct"
    llm_timeout_s: float = 60.0
    llm_temperature: float = 0.2

    # --- Retrieval ---------------------------------------------------------
    embedding_provider: str = "hash"  # hash | openai
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 384
    chunk_size: int = 420
    chunk_overlap: int = 80
    retrieve_top_k: int = 20
    rerank_top_k: int = 5
    rrf_k: int = 60
    max_subtasks: int = 4

    # --- Guard -------------------------------------------------------------
    citation_max_retry: int = 1
    low_confidence_threshold: float = 0.35

    # --- Optional stores (v0.2 adapters) -----------------------------------
    milvus_uri: str = ""
    neo4j_uri: str = ""
    s3_endpoint: str = ""

    def ensure_dirs(self) -> None:
        (self.data_dir / "uploads").mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
