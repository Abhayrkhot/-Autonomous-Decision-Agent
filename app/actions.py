"""Human-reviewed immutable outbox actions with conservative delivery semantics."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import Field

from app.auth import Principal
from app.models import Model
from app.postgres import PostgresRunStore


class ActionError(ValueError):
    pass


class Approval(Model):
    digest: str = Field(min_length=64, max_length=64)
    decision: Literal["approve", "reject"]


class Action(Model):
    action_id: UUID
    run_id: UUID
    owner_id: str
    payload: dict[str, str]
    digest: str
    status: Literal["pending", "approved", "rejected", "dispatching", "sent", "unknown"]
    expires_at: datetime


def decode(row) -> Action:
    return Action(**dict(zip(Action.model_fields, row, strict=True)))


class ActionService:
    def __init__(
        self,
        store: PostgresRunStore,
        webhook: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if webhook and not webhook.startswith("https://"):
            raise ValueError("Action webhook must use HTTPS")
        self.store, self.webhook, self.transport = store, webhook, transport

    async def propose(self, run_id: UUID, principal: Principal) -> Action:
        record = await self.store.get(run_id, principal.owner_id)
        if record is None:
            raise ActionError("Run not found")
        payload = {"run_id": str(run_id), "message": record.response.message}
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        action = Action(
            action_id=uuid4(),
            run_id=run_id,
            owner_id=principal.owner_id,
            payload=payload,
            digest=digest,
            status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        async with self.store.pool.connection() as conn:
            from psycopg.types.json import Jsonb

            await conn.execute(
                "INSERT INTO actions VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING",
                (
                    action.action_id,
                    run_id,
                    principal.owner_id,
                    Jsonb(payload),
                    digest,
                    action.status,
                    action.expires_at,
                ),
            )
            cursor = await conn.execute(
                "SELECT * FROM actions WHERE run_id=%s AND owner_id=%s",
                (run_id, principal.owner_id),
            )
            result = decode(await cursor.fetchone())
            if result.action_id == action.action_id:
                await conn.execute(
                    "INSERT INTO action_audit(action_id,actor,event) VALUES (%s,%s,'proposed')",
                    (action.action_id, principal.actor),
                )
        return result

    async def get(self, action_id: UUID, principal: Principal) -> Action:
        async with self.store.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM actions WHERE action_id=%s AND owner_id=%s",
                (action_id, principal.owner_id),
            )
            row = await cursor.fetchone()
            if not row:
                raise ActionError("Action not found")
            return decode(row)

    async def transition(
        self,
        action_id: UUID,
        principal: Principal,
        expected: str,
        target: str,
        digest: str | None = None,
    ) -> Action:
        async with self.store.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM actions WHERE action_id=%s AND owner_id=%s FOR UPDATE",
                (action_id, principal.owner_id),
            )
            row = await cursor.fetchone()
            if not row:
                raise ActionError("Action not found")
            action = decode(row)
            if action.status != expected or (
                digest is not None and digest != action.digest
            ):
                raise ActionError("Action state or digest mismatch")
            if target in (
                "approved",
                "dispatching",
            ) and action.expires_at <= datetime.now(timezone.utc):
                raise ActionError("Action expired")
            await conn.execute(
                "UPDATE actions SET status=%s WHERE action_id=%s", (target, action_id)
            )
            await conn.execute(
                "INSERT INTO action_audit(action_id,actor,event) VALUES (%s,%s,%s)",
                (action_id, principal.actor, target),
            )
            return action.model_copy(update={"status": target})

    async def approve(
        self, action_id: UUID, approval: Approval, principal: Principal
    ) -> Action:
        if principal.role != "reviewer":
            raise ActionError("Reviewer role required")
        return await self.transition(
            action_id,
            principal,
            "pending",
            "approved" if approval.decision == "approve" else "rejected",
            approval.digest,
        )

    async def deliver(self, action_id: UUID, principal: Principal) -> Action:
        if not self.webhook:
            raise ActionError("No action webhook configured")
        action = await self.transition(action_id, principal, "approved", "dispatching")
        status = "unknown"
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=10, follow_redirects=False
            ) as client:
                response = await client.post(
                    self.webhook,
                    json=action.payload,
                    headers={"Idempotency-Key": str(action.action_id)},
                )
                if 200 <= response.status_code < 300:
                    status = "sent"
        except httpx.HTTPError:
            pass  # Delivery may have happened: never retry an ambiguous side effect.
        return await self.transition(action_id, principal, "dispatching", status)
