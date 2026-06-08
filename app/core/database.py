import aiosqlite
from datetime import datetime, timezone

from app.db.settings import getSettings

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS mismatches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    primaryAction    TEXT,
    candidateAction  TEXT,
    primaryContent   TEXT,
    candidateContent TEXT
)
"""


def _dbPath() -> str:
    return getSettings().MISMATCH_DB_PATH


async def initDb() -> None:
    """
    Create the ``mismatches`` table if it does not already exist.

    Safe to call on every application startup; uses ``CREATE TABLE IF NOT EXISTS``.
    """
    async with aiosqlite.connect(_dbPath()) as db:
        await db.execute(_CREATE_TABLE)
        await db.commit()


async def recordMismatch(
    primaryAction: str | None,
    candidateAction: str | None,
    primaryContent: str,
    candidateContent: str,
) -> None:
    """
    Persist an action-key mismatch row to SQLite for offline debugging.

    :param primaryAction: The ``action`` value extracted from the primary response.
    :type primaryAction: str or None
    :param candidateAction: The ``action`` value extracted from the candidate response.
    :type candidateAction: str or None
    :param primaryContent: Raw content string from the primary LLM message.
    :type primaryContent: str
    :param candidateContent: Raw content string from the candidate LLM message.
    :type candidateContent: str
    """
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(_dbPath()) as db:
        await db.execute(
            "INSERT INTO mismatches "
            "(timestamp, primaryAction, candidateAction, primaryContent, candidateContent) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, primaryAction, candidateAction, primaryContent, candidateContent),
        )
        await db.commit()
