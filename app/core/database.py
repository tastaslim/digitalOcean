import aiosqlite
from datetime import datetime, timezone

DB_PATH = "mismatches.db"

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


async def initDb() -> None:
    """Create the mismatches table if it does not already exist."""
    async with aiosqlite.connect(DB_PATH) as db:
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

    Args:
        primaryAction: The `action` value extracted from the primary response.
        candidateAction: The `action` value extracted from the candidate response.
        primaryContent: Raw content string from the primary LLM message.
        candidateContent: Raw content string from the candidate LLM message.
    """
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO mismatches "
            "(timestamp, primaryAction, candidateAction, primaryContent, candidateContent) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, primaryAction, candidateAction, primaryContent, candidateContent),
        )
        await db.commit()
