"""Immutable human reviews and observed outcomes, scoped to a stored run."""

from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import Field, StrictBool

from app.auth import Principal
from app.models import Model
from app.postgres import PostgresRunStore


class FeedbackError(ValueError):
    pass


class HumanReview(Model):
    usefulness: int = Field(ge=1, le=5, strict=True)
    correct: StrictBool
    grounded: StrictBool
    safe: StrictBool
    notes: str = Field(default="", max_length=2000)
    rubric_version: str = "human-v1"


class Outcome(Model):
    event_id: UUID
    task_completed: StrictBool
    action_id: UUID | None = None
    notes: str = Field(default="", max_length=2000)


def agreement(votes: list[bool]) -> float | None:
    if len(votes) < 2:
        return None
    agreeing = sum(a == b for i, a in enumerate(votes) for b in votes[i + 1 :])
    pairs = len(votes) * (len(votes) - 1) / 2
    return agreeing / pairs


class FeedbackService:
    def __init__(self, store: PostgresRunStore):
        self.store = store

    async def require_run(self, run_id: UUID, principal: Principal) -> None:
        if await self.store.get(run_id, principal.owner_id) is None:
            raise FeedbackError("Run not found")

    async def review(
        self, run_id: UUID, review: HumanReview, principal: Principal
    ) -> HumanReview:
        await self.require_run(run_id, principal)
        if principal.role != "reviewer":
            raise FeedbackError("Reviewer role required")
        async with self.store.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO reviews(run_id,reviewer,body) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                (run_id, principal.actor, Jsonb(review.model_dump())),
            )
            cursor = await conn.execute(
                "SELECT body FROM reviews WHERE run_id=%s AND reviewer=%s",
                (run_id, principal.actor),
            )
            if (await cursor.fetchone())[0] != review.model_dump():
                raise FeedbackError("Review is immutable; conflicting duplicate")
        return review

    async def outcome(
        self, run_id: UUID, outcome: Outcome, principal: Principal
    ) -> Outcome:
        await self.require_run(run_id, principal)
        payload = outcome.model_dump(mode="json")
        async with self.store.pool.connection() as conn:
            if outcome.action_id:
                cursor = await conn.execute(
                    "SELECT action_id FROM actions WHERE action_id=%s AND run_id=%s AND owner_id=%s",
                    (outcome.action_id, run_id, principal.owner_id),
                )
                if await cursor.fetchone() is None:
                    raise FeedbackError("Action does not belong to this run")
            await conn.execute(
                "INSERT INTO feedback(owner_id,event_id,run_id,body) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (principal.owner_id, outcome.event_id, run_id, Jsonb(payload)),
            )
            cursor = await conn.execute(
                "SELECT run_id,body FROM feedback WHERE owner_id=%s AND event_id=%s",
                (principal.owner_id, outcome.event_id),
            )
            previous = await cursor.fetchone()
            if previous != (run_id, payload):
                raise FeedbackError("Feedback event conflicts with earlier observation")
        return outcome

    async def report(self, run_id: UUID, principal: Principal) -> dict:
        await self.require_run(run_id, principal)
        async with self.store.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT body FROM reviews WHERE run_id=%s ORDER BY reviewer LIMIT 1000",
                (run_id,),
            )
            reviews = [
                HumanReview.model_validate(row[0]) for row in await cursor.fetchall()
            ]
            cursor = await conn.execute(
                "SELECT body FROM feedback WHERE run_id=%s AND owner_id=%s ORDER BY created_at,event_id LIMIT 1000",
                (run_id, principal.owner_id),
            )
            outcomes = [
                Outcome.model_validate(row[0]) for row in await cursor.fetchall()
            ]
        return {
            "review_count": len(reviews),
            "outcome_count": len(outcomes),
            "mean_usefulness": sum(r.usefulness for r in reviews) / len(reviews)
            if reviews
            else None,
            "safety_agreement": agreement([r.safe for r in reviews]),
            "observed_completion_fraction": sum(o.task_completed for o in outcomes)
            / len(outcomes)
            if outcomes
            else None,
            "limit_per_category": 1000,
        }
