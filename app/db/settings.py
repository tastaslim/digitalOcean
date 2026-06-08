from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from ``cloud.env`` (and environment variables).

    All fields map directly to environment variable names.  Per-endpoint API
    keys fall back to the shared :attr:`API_KEY` when left blank.

    :param API_KEY: Shared fallback bearer token used when a per-endpoint key
        is not set.
    :type API_KEY: str
    :param PRIMARY_LLM_BASE_URL: Base URL of the primary LLM endpoint.
    :type PRIMARY_LLM_BASE_URL: str
    :param PRIMARY_LLM_API_KEY: Bearer token for the primary endpoint;
        falls back to :attr:`API_KEY` when blank.
    :type PRIMARY_LLM_API_KEY: str
    :param PRIMARY_LLM_MODEL: Model identifier sent to the primary endpoint.
    :type PRIMARY_LLM_MODEL: str
    :param CANDIDATE_LLM_BASE_URL: Base URL of the candidate (shadow) LLM endpoint.
    :type CANDIDATE_LLM_BASE_URL: str
    :param CANDIDATE_LLM_API_KEY: Bearer token for the candidate endpoint;
        falls back to :attr:`API_KEY` when blank.
    :type CANDIDATE_LLM_API_KEY: str
    :param CANDIDATE_LLM_MODEL: Model identifier sent to the candidate endpoint.
    :type CANDIDATE_LLM_MODEL: str
    :param SHADOW_TIMEOUT_SECONDS: Maximum seconds to wait for a candidate
        response before recording a timeout error.
    :type SHADOW_TIMEOUT_SECONDS: int
    :param MAX_CONCURRENT_SHADOWS: Pool cap — shadow tasks beyond this limit are
        dropped (load-shed) to protect the primary request path.
    :type MAX_CONCURRENT_SHADOWS: int
    :param MISMATCH_DB_PATH: Filesystem path for the SQLite mismatch database.
    :type MISMATCH_DB_PATH: str
    """

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
        """
        Return the effective API key for the primary LLM endpoint.

        :return: :attr:`PRIMARY_LLM_API_KEY` if set, otherwise :attr:`API_KEY`.
        :rtype: str
        """
        return self.PRIMARY_LLM_API_KEY or self.API_KEY

    def candidateKey(self) -> str:
        """
        Return the effective API key for the candidate LLM endpoint.

        :return: :attr:`CANDIDATE_LLM_API_KEY` if set, otherwise :attr:`API_KEY`.
        :rtype: str
        """
        return self.CANDIDATE_LLM_API_KEY or self.API_KEY


@lru_cache
def getSettings() -> Settings:
    """
    Return the cached :class:`Settings` instance loaded from ``cloud.env``.

    The result is memoised by :func:`functools.lru_cache`, so settings are
    read from disk exactly once per process.

    :return: Application settings singleton.
    :rtype: Settings
    """
    return Settings()  # pyright: ignore[reportCallIssue]
