"""Durable job queue with lease fencing. Redis notifications are an optimization."""

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.agent import DecisionAgent
from app.guardrails import validate_request
from app.knowledge import PostgresKnowledge
from app.models import AgentRequest, Model
from app.postgres import PostgresRunStore
from app.provider import DecisionProvider
from app.storage import InMemoryRunStore, RunRecord


class JobError(ValueError):
    pass


class QueueFull(JobError):
    pass


class JobStatus(Model):
    job_id: UUID
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    attempts: int
    created_at: datetime
    result_id: UUID | None
    error_code: str | None


def public(row: dict) -> JobStatus:
    return JobStatus.model_validate({key: row[key] for key in JobStatus.model_fields})


class JobService:
    def __init__(
        self,
        store: PostgresRunStore,
        redis_url: str | None = None,
        lease_seconds: float = 60,
        capacity: int = 100,
    ):
        if lease_seconds <= 0 or capacity < 1:
            raise ValueError("Invalid job limits")
        self.store, self.lease_seconds, self.capacity = store, lease_seconds, capacity
        self.redis = (
            Redis.from_url(redis_url, socket_connect_timeout=0.3, socket_timeout=2)
            if redis_url
            else None
        )

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()

    async def notify(self) -> None:
        if self.redis:
            try:
                async with self.redis.pipeline() as pipe:
                    await (
                        pipe.lpush("ada:wake", "1").ltrim("ada:wake", 0, 999).execute()
                    )
            except RedisError:
                pass  # The committed PostgreSQL job is authoritative.

    async def wait(self) -> None:
        if self.redis:
            try:
                await self.redis.blpop("ada:wake", timeout=1)
                return
            except RedisError:
                pass
        await asyncio.sleep(0.2)

    async def submit(self, request: AgentRequest, owner: str, key: str) -> JobStatus:
        validate_request(request)
        if not key.strip() or len(key) > 200:
            raise JobError("Invalid idempotency key")
        payload = request.model_dump(mode="json")
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        async with (
            self.store.pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", ("jobs:" + owner,)
            )
            await cur.execute(
                "SELECT * FROM jobs WHERE owner_id=%s AND idempotency_key=%s",
                (owner, key),
            )
            row = await cur.fetchone()
            if row:
                if row["digest"] != digest:
                    raise JobError("Idempotency key belongs to another request")
                return public(row)
            await cur.execute(
                "SELECT count(*) AS count FROM jobs WHERE owner_id=%s AND status IN ('queued','running')",
                (owner,),
            )
            if (await cur.fetchone())["count"] >= self.capacity:
                raise QueueFull("Too many pending jobs")
            await cur.execute(
                "INSERT INTO jobs(job_id,owner_id,idempotency_key,request,digest,status) VALUES (%s,%s,%s,%s,%s,'queued') RETURNING *",
                (uuid4(), owner, key, Jsonb(payload), digest),
            )
            result = public(await cur.fetchone())
        await self.notify()
        return result

    async def get(self, job_id: UUID, owner: str) -> JobStatus:
        async with (
            self.store.pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "SELECT * FROM jobs WHERE job_id=%s AND owner_id=%s", (job_id, owner)
            )
            row = await cur.fetchone()
            if not row:
                raise JobError("Job not found")
            return public(row)

    async def cancel(self, job_id: UUID, owner: str) -> JobStatus:
        async with (
            self.store.pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "UPDATE jobs SET status='cancelled',lease_token=NULL WHERE job_id=%s AND owner_id=%s AND status IN ('queued','running') RETURNING *",
                (job_id, owner),
            )
            row = await cur.fetchone()
            if not row:
                raise JobError("Job cannot be cancelled")
            return public(row)

    async def claim(self) -> dict | None:
        async with (
            self.store.pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            await cur.execute(
                "UPDATE jobs SET status='failed',error_code='lease_exhausted' WHERE status='running' AND lease_until<now() AND attempts>=3"
            )
            await cur.execute(
                "SELECT job_id FROM jobs WHERE attempts<3 AND ((status='queued' AND next_attempt_at<=now()) OR (status='running' AND lease_until<now())) ORDER BY created_at,job_id LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
            row = await cur.fetchone()
            if not row:
                return None
            await cur.execute(
                "UPDATE jobs SET status='running',attempts=attempts+1,lease_token=%s,lease_until=now()+(%s * interval '1 second') WHERE job_id=%s RETURNING *",
                (uuid4(), self.lease_seconds, row["job_id"]),
            )
            return await cur.fetchone()

    async def finish(self, job: dict, record: RunRecord | None) -> None:
        success = record is not None
        status = (
            "completed" if success else ("failed" if job["attempts"] >= 3 else "queued")
        )
        async with self.store.pool.connection() as conn:
            cursor = await conn.execute(
                "UPDATE jobs SET status=%s,result_id=%s,error_code=%s,next_attempt_at=now()+(%s * interval '1 second'),lease_token=NULL WHERE job_id=%s AND status='running' AND lease_token=%s AND lease_until>now()",
                (
                    status,
                    job["job_id"] if success else None,
                    None if success else "execution_failed",
                    2 ** job["attempts"],
                    job["job_id"],
                    job["lease_token"],
                ),
            )
            if cursor.rowcount != 1:
                raise JobError("Job lease lost")
            if record:
                record = record.model_copy(deep=True)
                record.response.run_id = job["job_id"]
                record.owner_id = job["owner_id"]
                await conn.execute(
                    "INSERT INTO runs VALUES (%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING",
                    (
                        job["job_id"],
                        record.owner_id,
                        record.created_at,
                        Jsonb(record.model_dump(mode="json")),
                    ),
                )

    async def run_once(self, provider: DecisionProvider | None = None) -> bool:
        job = await self.claim()
        if job is None:
            return False
        record = None
        try:
            request = AgentRequest.model_validate(job["request"])
            async with asyncio.timeout(min(45, self.lease_seconds * 0.8)):
                response = await DecisionAgent(
                    InMemoryRunStore(),
                    provider=provider,
                    knowledge=PostgresKnowledge(self.store),
                ).run(request, job["owner_id"])
            record = RunRecord(
                owner_id=job["owner_id"], request=request, response=response
            )
        except Exception:
            pass  # Persist a stable error code; never leak provider/customer details.
        try:
            await self.finish(job, record)
        except JobError:
            pass  # Another worker/cancellation fenced this attempt.
        return True
