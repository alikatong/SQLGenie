from __future__ import annotations

import os
import json
import math
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
INSECURE_DEFAULT_SECRET_KEY = "sqlgenie-dev-secret"
INSECURE_DEFAULT_ADMIN_PASSWORD = "admin123"
EXAMPLE_SECRET_KEY = "replace-with-a-long-random-secret"
EXAMPLE_ADMIN_PASSWORD = "replace-with-a-strong-admin-password"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
REASONING_EFFORT_VALUES = frozenset({"low", "medium", "high", "xhigh", "max"})
DEFAULT_REASONING_EFFORT: str | None = None
DEFAULT_PROMPT_MAX_CHARS = 60_000
MAX_PROMPT_MAX_CHARS = 120_000
DEFAULT_QWEN_EMBEDDING_MODEL = "models/Qwen3-Embedding-0.6B"
QWEN_EMBEDDING_FAMILY = "Qwen"


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


def validate_qwen_embedding_model_path(raw_value: object) -> str:
    """Validate and normalize a local Qwen SentenceTransformer directory."""
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError("必须配置 Qwen Embedding 模型本地目录。")

    model_path = Path(value).expanduser()
    if not model_path.is_absolute():
        model_path = BASE_DIR / model_path
    try:
        model_path = model_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError("Qwen Embedding 模型目录不存在。") from exc

    if not model_path.is_dir():
        raise ValueError("Qwen Embedding 模型路径必须是目录。")

    config_path = model_path / "config.json"
    if not config_path.is_file():
        raise ValueError("Qwen Embedding 模型目录缺少 config.json。")

    try:
        model_config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Qwen Embedding 模型 config.json 无法读取。") from exc

    model_markers = json.dumps(model_config, ensure_ascii=False).casefold()
    model_markers += f" {model_path.name.casefold()}"
    if "qwen" not in model_markers:
        raise ValueError("Embedding 模型必须是 Qwen 系列模型。")

    return str(model_path)


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_reasoning_effort(
    raw_value: object,
    default: str | None = DEFAULT_REASONING_EFFORT,
) -> str | None:
    """Normalize optional provider-specific reasoning strength values."""
    if raw_value is None:
        return default
    normalized = str(raw_value).strip().lower()
    if normalized in REASONING_EFFORT_VALUES:
        return normalized
    return default


def normalize_prompt_max_chars(raw_value: object, default: int = DEFAULT_PROMPT_MAX_CHARS) -> int:
    try:
        parsed = int(raw_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if parsed < 1_000:
        return 1_000
    return min(parsed, MAX_PROMPT_MAX_CHARS)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        return default
    return min(max(value, minimum), maximum)


def resolve_cors_origins(raw_value: object = None) -> list[str]:
    """Parse a comma-separated CORS origin list with a loopback fallback."""
    raw = str(
        raw_value
        if raw_value is not None
        else os.getenv("CORS_ORIGINS", ",".join(DEFAULT_CORS_ORIGINS))
    ).strip()
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or list(DEFAULT_CORS_ORIGINS)


def cors_allow_credentials(origins: list[str] | tuple[str, ...]) -> bool:
    """Credentials must never be combined with a wildcard origin."""
    return "*" not in origins


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
    llm_thinking_timeout_seconds: int = int(os.getenv("LLM_THINKING_TIMEOUT_SECONDS", "600"))
    llm_reasoning_effort: str | None = normalize_reasoning_effort(
        os.getenv("LLM_REASONING_EFFORT"),
    )
    llm_prompt_max_chars: int = _env_int(
        "LLM_PROMPT_MAX_CHARS",
        DEFAULT_PROMPT_MAX_CHARS,
        1_000,
        MAX_PROMPT_MAX_CHARS,
    )
    db_path: Path = _resolve_db_path(os.getenv("SQLGENIE_DB_PATH", "sqlgenie.db"))
    rag_embedding_model: str = os.getenv(
        "RAG_EMBEDDING_MODEL",
        DEFAULT_QWEN_EMBEDDING_MODEL,
    )
    rag_embedding_batch_size: int = min(max(int(os.getenv("RAG_EMBEDDING_BATCH_SIZE", "4")), 1), 32)
    rag_embedding_max_seq_length: int = min(
        max(int(os.getenv("RAG_EMBEDDING_MAX_SEQ_LENGTH", "1024")), 128),
        32_768,
    )
    rag_chroma_path: Path = _resolve_db_path(os.getenv("RAG_CHROMA_PATH", ".chroma"))
    rag_top_k: int = min(max(int(os.getenv("RAG_TOP_K", "8")), 1), 20)
    rag_expand_depth: int = _env_int("RAG_EXPAND_DEPTH", 1, 0, 3)
    rag_min_keyword_hits: int = _env_int("RAG_MIN_KEYWORD_HITS", 2, 1, 20)
    rag_min_keyword_score: float = _env_float("RAG_MIN_KEYWORD_SCORE", 6.0, 0.0, 1000.0)
    rag_min_vector_similarity: float = _env_float("RAG_MIN_VECTOR_SIMILARITY", 0.65, 0.0, 1.0)
    rag_min_vector_margin: float = _env_float("RAG_MIN_VECTOR_MARGIN", 0.08, 0.0, 1.0)
    his_term_top_k: int = _env_int("HIS_TERM_TOP_K", 8, 1, 20)
    feedback_rag_top_k: int = _env_int("FEEDBACK_RAG_TOP_K", 3, 1, 20)
    rag_collection_prefix: str = os.getenv("RAG_COLLECTION_PREFIX", "sqlgenie")
    login_rate_limit_max: int = _env_int("LOGIN_RATE_LIMIT_MAX", 20, 0, 10_000)
    login_rate_limit_window_seconds: int = _env_int(
        "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
        300,
        1,
        86_400,
    )
    generate_rate_limit_max: int = _env_int("GENERATE_RATE_LIMIT_MAX", 30, 0, 10_000)
    generate_rate_limit_window_seconds: int = _env_int(
        "GENERATE_RATE_LIMIT_WINDOW_SECONDS",
        60,
        1,
        86_400,
    )
    cors_origins: str = os.getenv(
        "CORS_ORIGINS",
        ",".join(DEFAULT_CORS_ORIGINS),
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return resolve_cors_origins(self.cors_origins)


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


def default_model_config() -> dict[str, str | bool | int | None]:
    return {
        "api_key": settings.llm_api_key,
        "base_url": settings.llm_base_url,
        "model_name": settings.llm_model,
        "enable_thinking": settings.llm_enable_thinking,
        "thinking_timeout_seconds": settings.llm_thinking_timeout_seconds,
        "reasoning_effort": settings.llm_reasoning_effort,
        "prompt_max_chars": settings.llm_prompt_max_chars,
        "rag_top_k": settings.rag_top_k,
        "feedback_rag_top_k": settings.feedback_rag_top_k,
        "embedding_model_path": settings.rag_embedding_model,
    }
