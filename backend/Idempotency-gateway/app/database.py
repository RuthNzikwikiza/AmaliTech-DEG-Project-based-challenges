import aiosqlite
import json
from pathlib import Path

DB_PATH = Path("idempotency.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS idempotency_records (
                key         TEXT PRIMARY KEY,
                body_hash   TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                response    TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'completed',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at  DATETIME GENERATED ALWAYS AS
                    (datetime(created_at, '+24 hours')) VIRTUAL
            )
        """)
        await db.commit()


async def get_record(key: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM idempotency_records WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None


async def insert_record(key: str, body_hash: str, status_code: int, response: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO idempotency_records (key, body_hash, status_code, response, status)
            VALUES (?, ?, ?, ?, 'completed')
            """,
            (key, body_hash, status_code, json.dumps(response)),
        )
        await db.commit()


async def insert_in_flight(key: str, body_hash: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO idempotency_records (key, body_hash, status_code, response, status)
            VALUES (?, ?, 0, '{}', 'processing')
            """,
            (key, body_hash),
        )
        await db.commit()


async def update_record(key: str, status_code: int, response: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE idempotency_records
            SET status_code = ?, response = ?, status = 'completed'
            WHERE key = ?
            """,
            (status_code, json.dumps(response), key),
        )
        await db.commit()


async def delete_expired():
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute(
            "DELETE FROM idempotency_records WHERE datetime('now') > expires_at"
        )
        await db.commit()
        return result.rowcount