import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from uuid import UUID

from app.domain.models.enums import Severity, TaskType
from app.ports.database import (
    MismatchRecord,
    MismatchRepository,
    ModelConfig,
    ModelFleetRepository,
)
from app.ports.shadowTask import ShadowTask, ShadowTaskRepository

logger = logging.getLogger(__name__)


class PostgresAdapter(MismatchRepository, ModelFleetRepository, ShadowTaskRepository):
    """
    PostgreSQL-backed persistence using asyncpg connection pool.

    Suitable for multi-node production deployments. The pool is created on
    :meth:`init` and shared across all requests served by this process.

    Implements MismatchRepository, ModelFleetRepository, and ShadowTaskRepository
    so the Container can inject the same object under all three port types while
    sharing one connection pool.

    Requires: pip install asyncpg
    DATABASE_URL format: postgresql://user:pass@host:5432/dbname
    """

    def __init__(self, databaseUrl: str) -> None:
        try:
            import asyncpg

            self._asyncpg = asyncpg
        except ImportError:
            raise RuntimeError("asyncpg not installed — run: pip install asyncpg")
        self._databaseUrl = databaseUrl
        self._pool = None

    async def init(self) -> None:
        self._pool = await self._asyncpg.create_pool(self._databaseUrl)
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS mismatches (
                    id               SERIAL PRIMARY KEY,
                    task_id          UUID   NOT NULL,
                    task_type        TEXT   NOT NULL,
                    primary_model    TEXT   NOT NULL,
                    candidate_model  TEXT   NOT NULL,
                    primary_action   TEXT,
                    candidate_action TEXT,
                    primary_content  TEXT,
                    candidate_content TEXT,
                    severity         TEXT   NOT NULL,
                    diff_fields      JSONB  NOT NULL DEFAULT '[]',
                    timestamp        TIMESTAMPTZ NOT NULL
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS model_fleet (
                    model_id   TEXT    PRIMARY KEY,
                    base_url   TEXT    NOT NULL,
                    api_key    TEXT    NOT NULL,
                    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
                    is_active  BOOLEAN NOT NULL DEFAULT TRUE,
                    weight     FLOAT   NOT NULL DEFAULT 1.0
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS shadow_tasks (
                    task_id                  TEXT         PRIMARY KEY,
                    task_type                TEXT         NOT NULL,
                    created_at               TIMESTAMPTZ  NOT NULL,

                    primary_s3_path          TEXT,
                    primary_model            TEXT,
                    primary_action           TEXT,
                    primary_content          TEXT,
                    primary_latency_ms       INTEGER,
                    is_primary_done          BOOLEAN      NOT NULL DEFAULT FALSE,
                    primary_completed_at     TIMESTAMPTZ,

                    candidate_s3_path        TEXT,
                    candidate_model          TEXT,
                    candidate_action         TEXT,
                    candidate_content        TEXT,
                    candidate_latency_ms     INTEGER,
                    is_candidate_done        BOOLEAN      NOT NULL DEFAULT FALSE,
                    candidate_completed_at   TIMESTAMPTZ,

                    is_comparison_triggered  BOOLEAN      NOT NULL DEFAULT FALSE,
                    is_comparison_done       BOOLEAN      NOT NULL DEFAULT FALSE,
                    comparison_result        TEXT,
                    comparison_completed_at  TIMESTAMPTZ
                )
            """)

    # ------------------------------------------------------------------
    # MismatchRepository
    # ------------------------------------------------------------------

    async def save(self, record: MismatchRecord) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO mismatches
                    (task_id, task_type, primary_model, candidate_model,
                     primary_action, candidate_action, primary_content,
                     candidate_content, severity, diff_fields, timestamp)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                RETURNING id
                """,
                record.taskId,
                record.taskType.value,
                record.primaryModel,
                record.candidateModel,
                record.primaryAction,
                record.candidateAction,
                record.primaryContent,
                record.candidateContent,
                record.severity.value,
                json.dumps(record.diffFields),
                record.timestamp,
            )
            return row["id"]

    async def reset(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM mismatches")
            await conn.execute("DELETE FROM shadow_tasks")

    async def findByTaskType(
        self,
        taskType: TaskType,
        severity: Optional[Severity] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MismatchRecord]:
        query = "SELECT * FROM mismatches WHERE task_type = $1"
        params: list = [taskType.value]
        if severity:
            params.append(severity.value)
            query += f" AND severity = ${len(params)}"
        params += [limit, offset]
        query += f" ORDER BY id DESC LIMIT ${len(params)-1} OFFSET ${len(params)}"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [
            MismatchRecord(
                id=r["id"],
                taskId=r["task_id"],
                taskType=TaskType(r["task_type"]),
                primaryModel=r["primary_model"],
                candidateModel=r["candidate_model"],
                primaryAction=r["primary_action"],
                candidateAction=r["candidate_action"],
                primaryContent=r["primary_content"],
                candidateContent=r["candidate_content"],
                severity=Severity(r["severity"]),
                diffFields=(
                    r["diff_fields"]
                    if isinstance(r["diff_fields"], list)
                    else json.loads(r["diff_fields"])
                ),
                timestamp=r["timestamp"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # ModelFleetRepository
    # ------------------------------------------------------------------

    async def getActiveModels(self) -> List[ModelConfig]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM model_fleet WHERE is_active = TRUE"
            )
        return [
            ModelConfig(
                modelId=r["model_id"],
                baseUrl=r["base_url"],
                apiKey=r["api_key"],
                isPrimary=r["is_primary"],
                isActive=r["is_active"],
                weight=r["weight"],
            )
            for r in rows
        ]

    async def registerModel(self, config: ModelConfig) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO model_fleet
                    (model_id, base_url, api_key, is_primary, is_active, weight)
                VALUES ($1,$2,$3,$4,$5,$6)
                ON CONFLICT (model_id) DO UPDATE SET
                    base_url   = EXCLUDED.base_url,
                    api_key    = EXCLUDED.api_key,
                    is_primary = EXCLUDED.is_primary,
                    is_active  = EXCLUDED.is_active,
                    weight     = EXCLUDED.weight
                """,
                config.modelId,
                config.baseUrl,
                config.apiKey,
                config.isPrimary,
                config.isActive,
                config.weight,
            )

    async def deactivateModel(self, modelId: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE model_fleet SET is_active = FALSE WHERE model_id = $1",
                modelId,
            )

    # ------------------------------------------------------------------
    # ShadowTaskRepository
    # ------------------------------------------------------------------

    async def upsertPrimaryDone(
        self,
        taskId: str,
        taskType: TaskType,
        primaryS3Path: str,
        primaryModel: str,
        primaryAction: str,
        primaryContent: str,
        primaryLatencyMs: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO shadow_tasks
                    (task_id, task_type, created_at,
                     primary_s3_path, primary_model, primary_action,
                     primary_content, primary_latency_ms,
                     is_primary_done, primary_completed_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,TRUE,$9)
                ON CONFLICT (task_id) DO UPDATE SET
                    primary_s3_path      = EXCLUDED.primary_s3_path,
                    primary_model        = EXCLUDED.primary_model,
                    primary_action       = EXCLUDED.primary_action,
                    primary_content      = EXCLUDED.primary_content,
                    primary_latency_ms   = EXCLUDED.primary_latency_ms,
                    is_primary_done      = TRUE,
                    primary_completed_at = EXCLUDED.primary_completed_at
                """,
                taskId, taskType.value, now,
                primaryS3Path, primaryModel, primaryAction,
                primaryContent, primaryLatencyMs, now,
            )

    async def upsertCandidateDone(
        self,
        taskId: str,
        taskType: TaskType,
        candidateS3Path: str,
        candidateModel: str,
        candidateAction: str,
        candidateContent: str,
        candidateLatencyMs: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO shadow_tasks
                    (task_id, task_type, created_at,
                     candidate_s3_path, candidate_model, candidate_action,
                     candidate_content, candidate_latency_ms,
                     is_candidate_done, candidate_completed_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,TRUE,$9)
                ON CONFLICT (task_id) DO UPDATE SET
                    candidate_s3_path      = EXCLUDED.candidate_s3_path,
                    candidate_model        = EXCLUDED.candidate_model,
                    candidate_action       = EXCLUDED.candidate_action,
                    candidate_content      = EXCLUDED.candidate_content,
                    candidate_latency_ms   = EXCLUDED.candidate_latency_ms,
                    is_candidate_done      = TRUE,
                    candidate_completed_at = EXCLUDED.candidate_completed_at
                """,
                taskId, taskType.value, now,
                candidateS3Path, candidateModel, candidateAction,
                candidateContent, candidateLatencyMs, now,
            )

    async def tryClaimComparison(self, taskId: str) -> bool:
        """
        Atomic UPDATE: set is_comparison_triggered=TRUE only when both sides are
        done and no one has claimed yet. asyncpg returns 'UPDATE N' — N=1 means
        this caller won.
        """
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                """
                UPDATE shadow_tasks
                   SET is_comparison_triggered = TRUE
                 WHERE task_id                 = $1
                   AND is_primary_done         = TRUE
                   AND is_candidate_done       = TRUE
                   AND is_comparison_triggered = FALSE
                """,
                taskId,
            )
        return status == "UPDATE 1"

    async def getTask(self, taskId: str) -> Optional[ShadowTask]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM shadow_tasks WHERE task_id = $1", taskId
            )
        if row is None:
            return None
        return _rowToShadowTask(row)

    async def markComparisonDone(self, taskId: str, comparisonResult: str) -> None:
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE shadow_tasks
                   SET is_comparison_done      = TRUE,
                       comparison_result       = $1,
                       comparison_completed_at = $2
                 WHERE task_id = $3
                """,
                comparisonResult, now, taskId,
            )

    async def findStalled(self, olderThanSeconds: int = 3600) -> List[ShadowTask]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=olderThanSeconds)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM shadow_tasks
                 WHERE is_comparison_done = FALSE
                   AND created_at < $1
                   AND (is_primary_done = TRUE OR is_candidate_done = TRUE)
                """,
                cutoff,
            )
        return [_rowToShadowTask(row) for row in rows]

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()


def _rowToShadowTask(row) -> ShadowTask:
    """Convert a shadow_tasks SELECT row to a ShadowTask (works for both asyncpg and aiosqlite)."""
    def _dt(val):
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        return datetime.fromisoformat(val)

    return ShadowTask(
        taskId=UUID(str(row["task_id"])),
        taskType=TaskType(row["task_type"]),
        createdAt=_dt(row["created_at"]),
        primaryS3Path=row["primary_s3_path"],
        primaryModel=row["primary_model"],
        primaryAction=row["primary_action"],
        primaryContent=row["primary_content"],
        primaryLatencyMs=row["primary_latency_ms"],
        isPrimaryDone=bool(row["is_primary_done"]),
        primaryCompletedAt=_dt(row["primary_completed_at"]),
        candidateS3Path=row["candidate_s3_path"],
        candidateModel=row["candidate_model"],
        candidateAction=row["candidate_action"],
        candidateContent=row["candidate_content"],
        candidateLatencyMs=row["candidate_latency_ms"],
        isCandidateDone=bool(row["is_candidate_done"]),
        candidateCompletedAt=_dt(row["candidate_completed_at"]),
        isComparisonTriggered=bool(row["is_comparison_triggered"]),
        isComparisonDone=bool(row["is_comparison_done"]),
        comparisonResult=row["comparison_result"],
        comparisonCompletedAt=_dt(row["comparison_completed_at"]),
    )
