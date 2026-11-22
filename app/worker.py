"""Start one worker with python -m app.worker; scale using more processes.

SIGTERM or SIGINT stops the loop after the current job instead of relying on task
cancellation, which Python 3.11's asyncio.wait_for can lose when it races with a
completing operation. An interrupted lease is reclaimed after it expires.
"""

import asyncio
import os
import signal
from pathlib import Path

from psycopg import Error
from psycopg_pool import PoolTimeout

from app.jobs import JobService
from app.postgres import PostgresRunStore
from app.provider import provider_from_env


async def pause(stop: asyncio.Event, seconds: float) -> None:
    try:
        async with asyncio.timeout(seconds):
            await stop.wait()
    except TimeoutError:
        pass


def heartbeat() -> None:
    # Liveness for the container health check: one touch per loop iteration.
    Path(os.getenv("WORKER_HEARTBEAT_FILE", "/tmp/ada-worker-heartbeat")).touch()


async def work(stop: asyncio.Event | None = None) -> None:
    stop = stop or asyncio.Event()
    provider = provider_from_env()
    store = PostgresRunStore(os.environ["DATABASE_URL"])
    jobs = JobService(store, os.getenv("REDIS_URL"))
    await store.open()
    try:
        while not stop.is_set():
            heartbeat()
            try:
                if not await jobs.run_once(provider):
                    await jobs.wait()
            except (Error, PoolTimeout):
                await pause(stop, 1)
    finally:
        await jobs.close()
        await store.close()


async def serve() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    await work(stop)


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":  # pragma: no cover -- thin wrapper; main is tested directly.
    main()
