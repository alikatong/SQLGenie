from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
INSECURE_DEFAULT_SECRET_KEY = "sqlgenie-dev-secret"
INSECURE_DEFAULT_ADMIN_PASSWORD = "admin123"
EXAMPLE_SECRET_KEY = "replace-with-a-long-random-secret"
EXAMPLE_ADMIN_PASSWORD = "replace-with-a-strong-admin-password"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _load_dotenv() -> None:
    if not ENV_FILE.exists():
        return

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _resolve_db_path(raw_value: str) -> Path:
    path = Path(raw_value)
    return path if path.is_absolute() else BASE_DIR / path


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


_load_dotenv()


@dataclass(slots=True)
class Settings:
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    secret_key: str = os.getenv("SECRET_KEY", INSECURE_DEFAULT_SECRET_KEY)
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", INSECURE_DEFAULT_ADMIN_PASSWORD)
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_enable_thinking: bool = _env_bool("LLM_ENABLE_THINKING", True)
    llm_thinking_timeout_seconds: int = int(os.getenv("LLM_THINKING_TIMEOUT_SECONDS", "120"))
    db_path: Path = _resolve_db_path(os.getenv("SQLGENIE_DB_PATH", "sqlgenie.db"))
    rag_embedding_model: str = os.getenv(
        "RAG_EMBEDDING_MODEL",
        "BAAI/bge-small-zh-v1.5",
    )
    rag_chroma_path: Path = _resolve_db_path(os.getenv("RAG_CHROMA_PATH", ".chroma"))
    rag_top_k: int = int(os.getenv("RAG_TOP_K", "30"))
    rag_expand_depth: int = int(os.getenv("RAG_EXPAND_DEPTH", "1"))
    rag_min_keyword_hits: int = int(os.getenv("RAG_MIN_KEYWORD_HITS", "2"))
    feedback_rag_top_k: int = int(os.getenv("FEEDBACK_RAG_TOP_K", "3"))
    rag_collection_prefix: str = os.getenv("RAG_COLLECTION_PREFIX", "sqlgenie")
    cors_origins: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


settings = Settings()


def validate_security_configuration() -> None:
    """Reject known credentials before exposing the API beyond localhost."""
    if settings.app_host.strip().lower() in LOOPBACK_HOSTS:
        return

    insecure_settings: list[str] = []
    if settings.secret_key in {INSECURE_DEFAULT_SECRET_KEY, EXAMPLE_SECRET_KEY}:
        insecure_settings.append("SECRET_KEY")
    if settings.admin_password in {INSECURE_DEFAULT_ADMIN_PASSWORD, EXAMPLE_ADMIN_PASSWORD}:
        insecure_settings.append("ADMIN_PASSWORD")

    if insecure_settings:
        names = ", ".join(insecure_settings)
        raise RuntimeError(
            f"Refusing to expose SQLGenie on the network with insecure defaults: {names}. "
            "Set strong values in .env before using a non-loopback APP_HOST."
        )


def default_model_config() -> dict[str, str | bool | int]:
    return {
        "api_key": settings.llm_api_key,
        "base_url": settings.llm_base_url,
        "model_name": settings.llm_model,
        "enable_thinking": settings.llm_enable_thinking,
        "thinking_timeout_seconds": settings.llm_thinking_timeout_seconds,
        "feedback_rag_top_k": settings.feedback_rag_top_k,
    }
