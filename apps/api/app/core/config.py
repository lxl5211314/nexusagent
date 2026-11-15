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

    # --- Storage backends --------------------------------------------------
    vector_backend: str = "local"  # local | milvus
    graph_backend: str = "local"  # local | neo4j
    graph_extractor: str = "rule"  # rule | llm
    max_tools_per_task: int = 6
    max_skills_per_task: int = 2
    ocr_backend: str = "none"  # none | mineru | paddle
    object_backend: str = "local"  # local | s3

    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: str = ""
    milvus_collection: str = "nexusagent_chunks"

    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "nexusagent"
    neo4j_database: str = "neo4j"

    mineru_base_url: str = "http://127.0.0.1:8888"
    mineru_api_key: str = ""
    mineru_timeout_s: float = 120.0

    paddle_base_url: str = "http://127.0.0.1:8080"
    paddle_timeout_s: float = 120.0

    # Empty means the in-process queue: single instance only. Set it to
    # redis://127.0.0.1:6379/0 to fan events out across processes.
    redis_url: str = ""

    # --- Extensions --------------------------------------------------------
    # A JSON file with {"servers": {...}}; empty means no MCP servers.
    mcp_config: str = ""
    mcp_timeout_s: float = 30.0
    # Let a subtask that retrieval could not answer consult a read-only MCP tool.
    mcp_tool_router: bool = True
    # When the external tool answers, append it to the sources so the answer can cite it.
    mcp_cite_external: bool = True
    # Directory of <name>/SKILL.md cards; empty means none.
    skills_dir: str = ""

    # S3/MinIO endpoint, credentials and bucket. Only read when
    # object_backend=s3; local mode keeps raw files under data/uploads.
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "nexusagent"
    s3_region: str = "us-east-1"

    def ensure_dirs(self) -> None:
        (self.data_dir / "uploads").mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
