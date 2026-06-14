import logging
from typing import Optional

from app.common.circuitBreaker import CircuitBreaker
from app.ports.blobStorage import BlobStoragePort
from app.ports.cache import CachePort
from app.ports.database import MismatchRepository, ModelFleetRepository
from app.ports.llm import LlmPort
from app.ports.messageQueue import MessageQueuePort

logger = logging.getLogger(__name__)


class Container:
    """
    Dependency injection container.

    Reads *_BACKEND settings and lazily constructs the matching adapter on
    first access. All adapters are singletons within one Container instance —
    meaning one process shares one connection pool, one in-memory store, etc.

    Swap the backend without touching application code: change QUEUE_BACKEND
    from 'memory' to 'sqs' in cloud.env and restart. The ports stay the same;
    only the adapter wired here changes.
    """

    def __init__(self, settings) -> None:
        self._settings = settings
        self._queue: Optional[MessageQueuePort] = None
        self._cache: Optional[CachePort] = None
        self._db = None          # implements MismatchRepository + ModelFleetRepository
        self._storage: Optional[BlobStoragePort] = None
        self._circuitBreaker: Optional[CircuitBreaker] = None
        self._primaryLlm: Optional[LlmPort] = None
        self._candidateLlm: Optional[LlmPort] = None

    # ------------------------------------------------------------------
    # Public port accessors
    # ------------------------------------------------------------------

    @property
    def queue(self) -> MessageQueuePort:
        if self._queue is None:
            self._queue = self._buildQueue()
        return self._queue

    @property
    def cache(self) -> CachePort:
        if self._cache is None:
            self._cache = self._buildCache()
        return self._cache

    @property
    def mismatchRepository(self) -> MismatchRepository:
        return self._ensureDb()

    @property
    def modelFleetRepository(self) -> ModelFleetRepository:
        return self._ensureDb()

    @property
    def storage(self) -> BlobStoragePort:
        if self._storage is None:
            self._storage = self._buildStorage()
        return self._storage

    @property
    def primaryLlm(self) -> LlmPort:
        if self._primaryLlm is None:
            from app.adapters.llm.openaiCompat import OpenAICompatAdapter
            self._primaryLlm = OpenAICompatAdapter(
                baseUrl=self._settings.PRIMARY_LLM_BASE_URL,
                apiKey=self._settings.primaryKey(),
                model=self._settings.PRIMARY_LLM_MODEL,
                timeoutSeconds=self._settings.PRIMARY_LLM_TIMEOUT_SECONDS,
            )
        return self._primaryLlm

    @property
    def candidateLlm(self) -> LlmPort:
        if self._candidateLlm is None:
            from app.adapters.llm.openaiCompat import OpenAICompatAdapter
            self._candidateLlm = OpenAICompatAdapter(
                baseUrl=self._settings.CANDIDATE_LLM_BASE_URL,
                apiKey=self._settings.candidateKey(),
                model=self._settings.CANDIDATE_LLM_MODEL,
                timeoutSeconds=self._settings.SHADOW_TIMEOUT_SECONDS,
            )
        return self._candidateLlm

    @property
    def circuitBreaker(self) -> CircuitBreaker:
        if self._circuitBreaker is None:
            self._circuitBreaker = CircuitBreaker(
                failureThreshold=self._settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
                recoveryTimeoutSeconds=self._settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS,
            )
        return self._circuitBreaker

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Run async initialisation (schema creation) for adapters that need it."""
        db = self._ensureDb()
        if hasattr(db, "init"):
            await db.init()

    async def close(self) -> None:
        """Gracefully release all adapter resources."""
        for adapter in (self._queue, self._cache, self._storage):
            if adapter is not None:
                try:
                    await adapter.close()
                except Exception:
                    logger.exception("Error closing adapter %s", type(adapter).__name__)
        if self._db is not None and hasattr(self._db, "close"):
            try:
                await self._db.close()
            except Exception:
                logger.exception("Error closing db adapter")

    # ------------------------------------------------------------------
    # Builders — one per infrastructure category
    # ------------------------------------------------------------------

    def _ensureDb(self):
        if self._db is None:
            self._db = self._buildDb()
        return self._db

    def _buildQueue(self) -> MessageQueuePort:
        backend = self._settings.QUEUE_BACKEND
        logger.info("Queue backend: %s", backend)
        if backend == "sqs":
            from app.adapters.queue.sqs import SQSAdapter
            return SQSAdapter(
                region=self._settings.AWS_REGION,
                sns_topic_arn=self._settings.SNS_SHADOW_TOPIC_ARN,
                aws_access_key_id=self._settings.AWS_ACCESS_KEY_ID or None,
                aws_secret_access_key=self._settings.AWS_SECRET_ACCESS_KEY or None,
                endpoint_url=self._settings.AWS_ENDPOINT_URL or None,
            )
        from app.adapters.queue.memory import InMemoryQueueAdapter
        return InMemoryQueueAdapter()

    def _buildCache(self) -> CachePort:
        backend = self._settings.CACHE_BACKEND
        logger.info("Cache backend: %s", backend)
        if backend == "redis":
            from app.adapters.cache.redis import RedisAdapter
            return RedisAdapter(url=self._settings.REDIS_URL)
        from app.adapters.cache.memory import InMemoryCacheAdapter
        return InMemoryCacheAdapter()

    def _buildDb(self):
        backend = self._settings.DB_BACKEND
        logger.info("Database backend: %s", backend)
        if backend == "postgres":
            from app.adapters.database.postgres import PostgresAdapter
            return PostgresAdapter(databaseUrl=self._settings.DATABASE_URL)
        from app.adapters.database.sqlite import SQLiteAdapter
        return SQLiteAdapter(dbPath=self._settings.MISMATCH_DB_PATH)

    def _buildStorage(self) -> BlobStoragePort:
        backend = self._settings.STORAGE_BACKEND
        logger.info("Storage backend: %s", backend)
        if backend == "s3":
            from app.adapters.storage.s3 import S3Adapter
            return S3Adapter(
                bucket=self._settings.S3_BUCKET,
                prefix=self._settings.S3_PREFIX,
                region=self._settings.AWS_REGION,
                aws_access_key_id=self._settings.AWS_ACCESS_KEY_ID or None,
                aws_secret_access_key=self._settings.AWS_SECRET_ACCESS_KEY or None,
                endpoint_url=self._settings.AWS_ENDPOINT_URL or None,
            )
        if backend == "azure":
            from app.adapters.storage.azureBlob import AzureBlobAdapter
            return AzureBlobAdapter(
                connection_string=self._settings.AZURE_STORAGE_CONNECTION_STRING,
                container_name=self._settings.AZURE_CONTAINER_NAME,
                prefix=self._settings.S3_PREFIX,
            )
        from app.adapters.storage.local import LocalFileAdapter
        return LocalFileAdapter(base_dir=self._settings.LOCAL_STORAGE_DIR)
