import os
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg.conninfo import make_conninfo

from app import postgres


def test_migration_cli_uses_configured_database(monkeypatch):
    calls = []

    async def migrate(dsn):
        calls.append(dsn)

    monkeypatch.setenv("DATABASE_URL", "postgresql://local-test")
    monkeypatch.setattr(postgres, "migrate", migrate)
    postgres.main()
    assert calls == ["postgresql://local-test"]


def test_migration_cli_requires_explicit_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(KeyError, match="DATABASE_URL"):
        postgres.main()


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL required"
)
async def test_fresh_schema_migration_is_complete_and_repeatable():
    dsn = os.environ["TEST_DATABASE_URL"]
    schema = "migration_test_" + uuid4().hex
    async with await AsyncConnection.connect(dsn, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            isolated_dsn = make_conninfo(dsn, options=f"-c search_path={schema}")
            await postgres.migrate(isolated_dsn)
            await postgres.migrate(isolated_dsn)
            async with await AsyncConnection.connect(isolated_dsn) as connection:
                cursor = await connection.execute(
                    "SELECT name FROM schema_migrations ORDER BY name"
                )
                assert [row[0] for row in await cursor.fetchall()] == sorted(
                    path.name for path in Path("migrations").glob("*.sql")
                )
                cursor = await connection.execute("SELECT count(*) FROM runs")
                assert await cursor.fetchone() == (0,)
        finally:
            await admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
