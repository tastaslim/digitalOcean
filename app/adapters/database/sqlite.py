import json
import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

import aiosqlite

from app.domain.models.enums import Severity, TaskType
from app.ports.database import (
    MismatchRecord,
    MismatchRepository,
    ModelConfig,
    ModelFleetRepository,
)

logger = logging.getLogger(__name__)


class SQLiteAdapter(MismatchRepository, ModelFleetRepository):
    """
    SQLite-backed persistence for mismatches and model fleet metadata.

    Suitable for local dev, tests, and single-node deployments.
    For multi-node production use PostgresAdapter instead.

    Implements both MismatchRepository and ModelFleetRepository so the Container
    can inject the same object under both port types while sharing one connection.
    """

    def __init__(self, dbPath: str) -> None:
        self._dbPath = dbPath

    async def init(self) -> None:
        async with aiosqlite.connect(self._dbPath) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS mismatches (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id         TEXT    NOT NULL,
                    task_type       TEXT    NOT NULL,
                    primary_model   TEXT    NOT NULL,
                    candidate_model TEXT    NOT NULL,
                    primary_action  TEXT,
                    candidate_action TEXT,
                    primary_content  TEXT,
                    candidate_content TEXT,
                    severity        TEXT    NOT NULL,
                    diff_fields     TEXT    NOT NULL,
                    timestamp       TEXT    NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS model_fleet (
                    model_id   TEXT    PRIMARY KEY,
                    base_url   TEXT    NOT NULL,
                    api_key    TEXT    NOT NULL,
                    is_primary INTEGER NOT NULL DEFAULT 0,
                    is_active  INTEGER NOT NULL DEFAULT 1,
                    weight     REAL    NOT NULL DEFAULT 1.0
                )
            """)
            await db.commit()

    # ------------------------------------------------------------------
    # MismatchRepository
    # ------------------------------------------------------------------

    async def save(self, record: MismatchRecord) -> int:
        async with aiosqlite.connect(self._dbPath) as db:
            cursor = await db.execute(
                """
                INSERT INTO mismatches
                    (task_id, task_type, primary_model, candidate_model,
                     primary_action, candidate_action, primary_content,
                     candidate_content, severity, diff_fields, timestamp)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(record.taskId),
                    record.taskType.value,
                    record.primaryModel,
                    record.candidateModel,
                    record.primaryAction,
                    record.candidateAction,
                    record.primaryContent,
                    record.candidateContent,
                    record.severity.value,
                    json.dumps(record.diffFields),
                    record.timestamp.isoformat(),
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def reset(self) -> None:
        async with aiosqlite.connect(self._dbPath) as db:
            await db.execute("DELETE FROM mismatches")
            await db.commit()

    async def findByTaskType(
        self,
        taskType: TaskType,
        severity: Optional[Severity] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MismatchRecord]:
        query = "SELECT * FROM mismatches WHERE task_type = ?"
        params: list = [taskType.value]
        if severity:
            query += " AND severity = ?"
            params.append(severity.value)
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]

        async with aiosqlite.connect(self._dbPath) as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

        return [
            MismatchRecord(
                id=row[0],
                taskId=UUID(row[1]),
                taskType=TaskType(row[2]),
                primaryModel=row[3],
                candidateModel=row[4],
                primaryAction=row[5],
                candidateAction=row[6],
                primaryContent=row[7],
                candidateContent=row[8],
                severity=Severity(row[9]),
                diffFields=json.loads(row[10]),
                timestamp=datetime.fromisoformat(row[11]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # ModelFleetRepository
    # ------------------------------------------------------------------

    async def getActiveModels(self) -> List[ModelConfig]:
        async with aiosqlite.connect(self._dbPath) as db:
            async with db.execute(
                "SELECT * FROM model_fleet WHERE is_active = 1"
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            ModelConfig(
                modelId=row[0],
                baseUrl=row[1],
                apiKey=row[2],
                isPrimary=bool(row[3]),
                isActive=bool(row[4]),
                weight=row[5],
            )
            for row in rows
        ]

    async def registerModel(self, config: ModelConfig) -> None:
        async with aiosqlite.connect(self._dbPath) as db:
            await db.execute(
                """
                INSERT INTO model_fleet
                    (model_id, base_url, api_key, is_primary, is_active, weight)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(model_id) DO UPDATE SET
                    base_url   = excluded.base_url,
                    api_key    = excluded.api_key,
                    is_primary = excluded.is_primary,
                    is_active  = excluded.is_active,
                    weight     = excluded.weight
                """,
                (
                    config.modelId,
                    config.baseUrl,
                    config.apiKey,
                    int(config.isPrimary),
                    int(config.isActive),
                    config.weight,
                ),
            )
            await db.commit()

    async def deactivateModel(self, modelId: str) -> None:
        async with aiosqlite.connect(self._dbPath) as db:
            await db.execute(
                "UPDATE model_fleet SET is_active = 0 WHERE model_id = ?",
                (modelId,),
            )
            await db.commit()
