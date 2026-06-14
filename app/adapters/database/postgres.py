import json
import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from app.domain.models.enums import Severity, TaskType
from app.ports.database import (
    MismatchRecord,
    MismatchRepository,
    ModelConfig,
    ModelFleetRepository,
)

logger = logging.getLogger(__name__)


class PostgresAdapter(MismatchRepository, ModelFleetRepository):
    """
    PostgreSQL-backed persistence using asyncpg connection pool.

    Suitable for multi-node production deployments. The pool is created on
    :meth:`init` and shared across all requests served by this process.

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

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
