"""Start one worker with python -m app.worker; scale using more processes."""

import asyncio
import os

from psycopg import Error
from psycopg_pool import PoolTimeout

from app.jobs import JobService
from app.postgres import PostgresRunStore
from app.provider import OpenAIProvider


async def work() -> None:
    store = PostgresRunStore(os.environ["DATABASE_URL"])
    jobs = JobService(store, os.getenv("REDIS_URL"))
    provider = None
    if os.getenv("AGENT_MODE", "deterministic") == "openai":
        provider = OpenAIProvider(
            os.environ["OPENAI_API_KEY"], os.environ["OPENAI_MODEL"]
        )
    await store.open()
    try:
        while True:
            try:
                if not await jobs.run_once(provider):
                    await jobs.wait()
            except (Error, PoolTimeout):
                await asyncio.sleep(1)
    finally:
        await jobs.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(work())
