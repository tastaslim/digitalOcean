from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="cloud.env", extra="ignore")

    API_KEY: str = ""

    PRIMARY_LLM_BASE_URL: str
    PRIMARY_LLM_API_KEY: str = ""
    PRIMARY_LLM_MODEL: str

    CANDIDATE_LLM_BASE_URL: str
    CANDIDATE_LLM_API_KEY: str = ""
    CANDIDATE_LLM_MODEL: str

    SHADOW_TIMEOUT_SECONDS: int = 30
    MAX_CONCURRENT_SHADOWS: int = 50
    MISMATCH_DB_PATH: str = "mismatches.db"

    def primaryKey(self) -> str:
        """Return the effective API key for the primary LLM endpoint."""
        return self.PRIMARY_LLM_API_KEY or self.API_KEY

    def candidateKey(self) -> str:
        """Return the effective API key for the candidate LLM endpoint."""
        return self.CANDIDATE_LLM_API_KEY or self.API_KEY


@lru_cache
def getSettings() -> Settings:
    """Return the cached application settings loaded from cloud.env."""
    return Settings()  # pyright: ignore[reportCallIssue]
