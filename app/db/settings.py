import os
import re
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _expandRef(val: str) -> str:
    """Expand ${VAR_NAME} references using os.environ (same as shell interpolation).
    Pydantic-settings loads .env files verbatim and does not expand ${} references,
    so we handle it here for the two API key helpers."""
    m = re.match(r"^\$\{(\w+)\}$", val.strip())
    return os.environ.get(m.group(1), val) if m else val


class Settings(BaseSettings):
    """
    Application settings loaded from ``cloud.env`` (and environment variables).

    Backend selection fields control which adapter the Container instantiates:
    - ``QUEUE_BACKEND``:   memory | sqs
    - ``CACHE_BACKEND``:   memory | redis
    - ``DB_BACKEND``:      sqlite | postgres
    - ``STORAGE_BACKEND``: local  | s3    | azure

    All per-endpoint API keys fall back to the shared ``API_KEY`` when left blank.
    """

    model_config = SettingsConfigDict(env_file="cloud.env", extra="ignore")

    # ------------------------------------------------------------------
    # LLM endpoints
    # ------------------------------------------------------------------

    API_KEY: str = ""

    PRIMARY_LLM_BASE_URL: str
    PRIMARY_LLM_API_KEY: str = ""
    PRIMARY_LLM_MODEL: str

    CANDIDATE_LLM_BASE_URL: str
    CANDIDATE_LLM_API_KEY: str = ""
    CANDIDATE_LLM_MODEL: str

    SHADOW_TIMEOUT_SECONDS: int = 30
    MAX_CONCURRENT_SHADOWS: int = 50
    PRIMARY_LLM_TIMEOUT_SECONDS: int = 30

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    # Clients must send X-API-Key: <value> on every request.
    # Leave blank to disable auth (dev / local testing only).
    PROXY_API_KEY: str = ""

    # ------------------------------------------------------------------
    # Circuit breaker — primary LLM
    # ------------------------------------------------------------------

    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS: int = 30

    # ------------------------------------------------------------------
    # Content storage limits
    # ------------------------------------------------------------------

    # Mismatch DB rows store a preview of LLM responses; full text lives in S3.
    # Raising this does not require a migration — TEXT columns have no length limit.
    CONTENT_MAX_CHARS: int = 2000

    # ------------------------------------------------------------------
    # Backend selection — swap without touching application code
    # ------------------------------------------------------------------

    QUEUE_BACKEND: str = "memory"     # memory | sqs
    CACHE_BACKEND: str = "memory"     # memory | redis
    DB_BACKEND: str = "sqlite"        # sqlite | postgres
    STORAGE_BACKEND: str = "local"    # local  | s3   | azure

    # ------------------------------------------------------------------
    # SQLite (DB_BACKEND=sqlite, default)
    # ------------------------------------------------------------------

    MISMATCH_DB_PATH: str = "mismatches.db"

    # ------------------------------------------------------------------
    # Local storage (STORAGE_BACKEND=local, default)
    # ------------------------------------------------------------------

    LOCAL_STORAGE_DIR: str = ".shadow_storage"

    # ------------------------------------------------------------------
    # AWS — used by SQSAdapter and S3Adapter
    # ------------------------------------------------------------------

    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # SQS fan-out: all shadow events go to this SNS topic, which routes to
    # per-model SQS queues via infrastructure-managed subscriptions.
    SNS_SHADOW_TOPIC_ARN: str = ""

    # Optional endpoint override — set to http://localstack:4566 when running
    # LocalStack so boto3 targets the local emulator instead of real AWS.
    AWS_ENDPOINT_URL: str = ""

    # S3 archive for replay
    S3_BUCKET: str = ""
    S3_PREFIX: str = "shadow-events"

    # ------------------------------------------------------------------
    # Redis (CACHE_BACKEND=redis)
    # ------------------------------------------------------------------

    REDIS_URL: str = "redis://localhost:6379"

    # ------------------------------------------------------------------
    # PostgreSQL (DB_BACKEND=postgres)
    # ------------------------------------------------------------------

    DATABASE_URL: str = ""

    # ------------------------------------------------------------------
    # Azure Blob (STORAGE_BACKEND=azure)
    # ------------------------------------------------------------------

    AZURE_STORAGE_CONNECTION_STRING: str = ""
    AZURE_CONTAINER_NAME: str = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def primaryKey(self) -> str:
        return _expandRef(self.PRIMARY_LLM_API_KEY or self.API_KEY)

    def candidateKey(self) -> str:
        return _expandRef(self.CANDIDATE_LLM_API_KEY or self.API_KEY)


@lru_cache
def getSettings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
