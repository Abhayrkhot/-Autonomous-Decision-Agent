"""Transactional PostgreSQL adapter and explicit, checksummed SQL migrations."""

import asyncio
import hashlib
import os
from pathlib import Path
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.storage import RunRecord, validate_page


async def migrate(dsn: str) -> None:
    async with await AsyncConnection.connect(dsn) as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(741209)")
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, checksum text NOT NULL)"
        )
        for path in sorted(
            (Path(__file__).resolve().parent.parent / "migrations").glob("*.sql")
        ):
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            cursor = await connection.execute(
                "SELECT checksum FROM schema_migrations WHERE name = %s", (path.name,)
            )
            applied = await cursor.fetchone()
            if applied:
                if applied[0] != checksum:
                    raise ValueError("Applied migration checksum changed")
                continue
            await connection.execute(path.read_text())
            await connection.execute(
                "INSERT INTO schema_migrations VALUES (%s, %s)", (path.name, checksum)
            )


class PostgresRunStore:
    def __init__(self, dsn: str, max_size: int = 5, timeout: float = 5) -> None:
        self.pool = AsyncConnectionPool(
            dsn,
            min_size=1,
            max_size=max_size,
            timeout=timeout,
            open=False,
            kwargs={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
        )

    async def open(self) -> None:
        await self.pool.open(wait=True, timeout=10)

    async def close(self) -> None:
        await self.pool.close()

    async def save(self, record: RunRecord) -> None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "INSERT INTO runs VALUES (%s, %s, %s, %s) ON CONFLICT (run_id) DO UPDATE SET record = EXCLUDED.record WHERE runs.owner_id = EXCLUDED.owner_id",
                (
                    record.response.run_id,
                    record.owner_id,
                    record.created_at,
                    Jsonb(record.model_dump(mode="json")),
                ),
            )

            if cursor.rowcount == 1:
                return
            raise ValueError("Run owner cannot change")

    async def get(self, run_id: UUID, owner_id: str = "local") -> RunRecord | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT record FROM runs WHERE run_id = %s AND owner_id = %s",
                (run_id, owner_id),
            )
            row = await cursor.fetchone()
            return RunRecord.model_validate(row[0]) if row else None

    async def list(
        self, owner_id: str = "local", limit: int = 20, offset: int = 0
    ) -> list[RunRecord]:
        validate_page(limit, offset)
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT record FROM runs WHERE owner_id = %s ORDER BY created_at DESC, run_id DESC LIMIT %s OFFSET %s",
                (owner_id, limit, offset),
            )
            return [RunRecord.model_validate(row[0]) for row in await cursor.fetchall()]


def main() -> None:
    """Apply migrations to the explicitly configured database."""
    asyncio.run(migrate(os.environ["DATABASE_URL"]))


if __name__ == "__main__":  # pragma: no cover -- thin wrapper; main is tested directly.
    main()
