import json
import logging
from datetime import datetime, timezone, timedelta
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
from app.ports.shadowTask import ShadowTask, ShadowTaskRepository

logger = logging.getLogger(__name__)

# aiosqlite.connect(timeout=N) sets SQLite's busy handler: wait up to N seconds
# before raising OperationalError("database is locked") when another writer holds
# the write lock. 0.5 s is enough for all in-process test scenarios.
_SQLITE_TIMEOUT = 0.5


class SQLiteAdapter(MismatchRepository, ModelFleetRepository, ShadowTaskRepository):
    """
    SQLite-backed persistence for mismatches, model fleet metadata, and shadow
    task checkpoints.

    Suitable for local dev, tests, and single-node deployments.
    For multi-node production use PostgresAdapter instead.

    Implements MismatchRepository, ModelFleetRepository, and ShadowTaskRepository
    so the Container can inject the same object under all three port types while
    sharing one database file.
    """

    def __init__(self, dbPath: str) -> None:
        self._dbPath = dbPath

    def _conn(self):
        """Open a connection with busy-wait so concurrent writers queue instead of crashing."""
        return aiosqlite.connect(self._dbPath, timeout=_SQLITE_TIMEOUT)

    async def init(self) -> None:
        async with self._conn() as db:
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
            await db.execute("""
                CREATE TABLE IF NOT EXISTS shadow_tasks (
                    task_id                  TEXT    PRIMARY KEY,
                    task_type                TEXT    NOT NULL,
                    created_at               TEXT    NOT NULL,

                    primary_s3_path          TEXT,
                    primary_model            TEXT,
                    primary_action           TEXT,
                    primary_content          TEXT,
                    primary_latency_ms       INTEGER,
                    is_primary_done          INTEGER NOT NULL DEFAULT 0,
                    primary_completed_at     TEXT,

                    candidate_s3_path        TEXT,
                    candidate_model          TEXT,
                    candidate_action         TEXT,
                    candidate_content        TEXT,
                    candidate_latency_ms     INTEGER,
                    is_candidate_done        INTEGER NOT NULL DEFAULT 0,
                    candidate_completed_at   TEXT,

                    is_comparison_triggered  INTEGER NOT NULL DEFAULT 0,
                    is_comparison_done       INTEGER NOT NULL DEFAULT 0,
                    comparison_result        TEXT,
                    comparison_completed_at  TEXT
                )
            """)
            await db.commit()

    # ------------------------------------------------------------------
    # MismatchRepository
    # ------------------------------------------------------------------

    async def save(self, record: MismatchRecord) -> int:
        async with self._conn() as db:
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
        async with self._conn() as db:
            await db.execute("DELETE FROM mismatches")
            await db.execute("DELETE FROM shadow_tasks")
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

        async with self._conn() as db:
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
        async with self._conn() as db:
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
        async with self._conn() as db:
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
        async with self._conn() as db:
            await db.execute(
                "UPDATE model_fleet SET is_active = 0 WHERE model_id = ?",
                (modelId,),
            )
            await db.commit()

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
        now = datetime.now(timezone.utc).isoformat()
        async with self._conn() as db:
            await db.execute(
                """
                INSERT INTO shadow_tasks
                    (task_id, task_type, created_at,
                     primary_s3_path, primary_model, primary_action,
                     primary_content, primary_latency_ms,
                     is_primary_done, primary_completed_at)
                VALUES (?,?,?,?,?,?,?,?,1,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    primary_s3_path      = excluded.primary_s3_path,
                    primary_model        = excluded.primary_model,
                    primary_action       = excluded.primary_action,
                    primary_content      = excluded.primary_content,
                    primary_latency_ms   = excluded.primary_latency_ms,
                    is_primary_done      = 1,
                    primary_completed_at = excluded.primary_completed_at
                """,
                (taskId, taskType.value, now,
                 primaryS3Path, primaryModel, primaryAction,
                 primaryContent, primaryLatencyMs, now),
            )
            await db.commit()

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
        now = datetime.now(timezone.utc).isoformat()
        async with self._conn() as db:
            await db.execute(
                """
                INSERT INTO shadow_tasks
                    (task_id, task_type, created_at,
                     candidate_s3_path, candidate_model, candidate_action,
                     candidate_content, candidate_latency_ms,
                     is_candidate_done, candidate_completed_at)
                VALUES (?,?,?,?,?,?,?,?,1,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    candidate_s3_path      = excluded.candidate_s3_path,
                    candidate_model        = excluded.candidate_model,
                    candidate_action       = excluded.candidate_action,
                    candidate_content      = excluded.candidate_content,
                    candidate_latency_ms   = excluded.candidate_latency_ms,
                    is_candidate_done      = 1,
                    candidate_completed_at = excluded.candidate_completed_at
                """,
                (taskId, taskType.value, now,
                 candidateS3Path, candidateModel, candidateAction,
                 candidateContent, candidateLatencyMs, now),
            )
            await db.commit()

    async def tryClaimComparison(self, taskId: str) -> bool:
        """
        Atomic UPDATE: set is_comparison_triggered=1 only when both sides are
        done and no one has claimed yet. rowcount > 0 means this caller won.
        """
        async with self._conn() as db:
            cursor = await db.execute(
                """
                UPDATE shadow_tasks
                   SET is_comparison_triggered = 1
                 WHERE task_id               = ?
                   AND is_primary_done       = 1
                   AND is_candidate_done     = 1
                   AND is_comparison_triggered = 0
                """,
                (taskId,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def getTask(self, taskId: str) -> Optional[ShadowTask]:
        async with self._conn() as db:
            async with db.execute(
                "SELECT * FROM shadow_tasks WHERE task_id = ?", (taskId,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return _rowToShadowTask(row)

    async def markComparisonDone(self, taskId: str, comparisonResult: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._conn() as db:
            await db.execute(
                """
                UPDATE shadow_tasks
                   SET is_comparison_done      = 1,
                       comparison_result       = ?,
                       comparison_completed_at = ?
                 WHERE task_id = ?
                """,
                (comparisonResult, now, taskId),
            )
            await db.commit()

    async def findStalled(self, olderThanSeconds: int = 3600) -> List[ShadowTask]:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=olderThanSeconds)).isoformat()
        async with self._conn() as db:
            async with db.execute(
                """
                SELECT * FROM shadow_tasks
                 WHERE is_comparison_done = 0
                   AND created_at < ?
                   AND (is_primary_done = 1 OR is_candidate_done = 1)
                """,
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_rowToShadowTask(row) for row in rows]


def _rowToShadowTask(row) -> ShadowTask:
    """Convert a shadow_tasks SELECT row (by column position) to a ShadowTask."""
    return ShadowTask(
        taskId=UUID(row[0]),
        taskType=TaskType(row[1]),
        createdAt=datetime.fromisoformat(row[2]),
        primaryS3Path=row[3],
        primaryModel=row[4],
        primaryAction=row[5],
        primaryContent=row[6],
        primaryLatencyMs=row[7],
        isPrimaryDone=bool(row[8]),
        primaryCompletedAt=datetime.fromisoformat(row[9]) if row[9] else None,
        candidateS3Path=row[10],
        candidateModel=row[11],
        candidateAction=row[12],
        candidateContent=row[13],
        candidateLatencyMs=row[14],
        isCandidateDone=bool(row[15]),
        candidateCompletedAt=datetime.fromisoformat(row[16]) if row[16] else None,
        isComparisonTriggered=bool(row[17]),
        isComparisonDone=bool(row[18]),
        comparisonResult=row[19],
        comparisonCompletedAt=datetime.fromisoformat(row[20]) if row[20] else None,
    )
