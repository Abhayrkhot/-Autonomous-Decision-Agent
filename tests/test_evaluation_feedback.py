import asyncio
import copy
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.actions import ActionService
from app.agent import DecisionAgent
from app.auth import Principal
from app.evaluation_suite import require_no_regression, run_suite
from app.feedback import FeedbackError, FeedbackService, HumanReview, Outcome, agreement
from app.main import create_app
from app.models import AgentRequest
from app.postgres import PostgresRunStore, migrate

DSN = os.getenv("TEST_DATABASE_URL")


def test_agreement_hand_calculation():
    assert agreement([]) is None
    assert agreement([True]) is None
    assert agreement([True, True]) == 1
    assert agreement([True, False]) == 0
    assert agreement([True, True, False]) == pytest.approx(1 / 3)


def test_evaluation_determinism_regression_and_metric_gaming(tmp_path):
    async def scenario():
        path = Path("tests/fixtures/evaluation-v1.json")
        reports = await asyncio.gather(*(run_suite(path) for _ in range(30)))
        assert all(report == reports[0] for report in reports)
        report = reports[0]
        assert report["all_passed"]
        require_no_regression(report, report)
        for mutation in ["hash", "cases", "metrics", "bad"]:
            candidate = copy.deepcopy(report)
            if mutation == "hash":
                candidate["dataset_sha256"] = "different"
            elif mutation == "cases":
                candidate["cases"].pop()
            elif mutation == "metrics":
                candidate["cases"][0]["checks"].pop("citation_valid")
            else:
                candidate["cases"][0]["checks"]["action_correct"] = False
                candidate["all_passed"] = (
                    True  # Aggregate flags cannot hide a bad case.
                )
            with pytest.raises(ValueError):
                require_no_regression(report, candidate)
        dataset = json.loads(path.read_text())
        dataset["cases"][0]["expected_action"] = "wrong"
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(dataset))
        assert not (await run_suite(bad))["all_passed"]
        dataset["cases"] = []
        bad.write_text(json.dumps(dataset))
        with pytest.raises(ValueError):
            await run_suite(bad)

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="real PostgreSQL required")
def test_human_review_outcome_integrity_and_report():
    async def scenario():
        await migrate(DSN)
        store = PostgresRunStore(DSN)
        await store.open()
        try:
            principal = Principal(owner_id=str(uuid4()), actor="r1", role="reviewer")
            req = AgentRequest(objective="help", context={"details": "help"})
            run = await DecisionAgent(store).run(req, principal.owner_id)
            feedback = FeedbackService(store)
            assert (await feedback.report(run.run_id, principal))[
                "mean_usefulness"
            ] is None
            review = HumanReview(usefulness=4, correct=True, grounded=True, safe=True)
            await feedback.review(run.run_id, review, principal)
            assert await feedback.review(run.run_id, review, principal) == review
            with pytest.raises(FeedbackError):
                await feedback.review(
                    run.run_id, review.model_copy(update={"safe": False}), principal
                )
            with pytest.raises(FeedbackError):
                await feedback.review(
                    run.run_id, review, principal.model_copy(update={"role": "user"})
                )
            with pytest.raises(FeedbackError):
                await feedback.report(
                    run.run_id, principal.model_copy(update={"owner_id": "other"})
                )
            await feedback.review(
                run.run_id,
                review.model_copy(update={"usefulness": 2, "safe": False}),
                principal.model_copy(update={"actor": "r2"}),
            )
            action = await ActionService(store).propose(run.run_id, principal)
            outcome = Outcome(
                event_id=uuid4(), task_completed=True, action_id=action.action_id
            )
            await feedback.outcome(run.run_id, outcome, principal)
            assert await feedback.outcome(run.run_id, outcome, principal) == outcome
            with pytest.raises(FeedbackError):
                await feedback.outcome(
                    run.run_id,
                    outcome.model_copy(update={"task_completed": False}),
                    principal,
                )
            with pytest.raises(FeedbackError):
                await feedback.outcome(
                    run.run_id,
                    outcome.model_copy(update={"action_id": uuid4()}),
                    principal,
                )
            await feedback.outcome(
                run.run_id, Outcome(event_id=uuid4(), task_completed=False), principal
            )
            report = await feedback.report(run.run_id, principal)
            assert report["mean_usefulness"] == 3
            assert report["safety_agreement"] == 0
            assert report["observed_completion_fraction"] == 0.5
        finally:
            await store.close()

    asyncio.run(scenario())


def test_feedback_api(monkeypatch):
    review = {"usefulness": 5, "correct": True, "grounded": True, "safe": True}
    with TestClient(create_app()) as client:
        assert (
            client.post(f"/agent/runs/{uuid4()}/reviews", json=review).status_code
            == 503
        )
    if not DSN:
        pytest.skip("real PostgreSQL required")
    monkeypatch.setenv("DATABASE_URL", DSN)
    token = "r" * 32
    monkeypatch.setenv(
        "API_TOKENS_JSON",
        json.dumps(
            {token: {"owner_id": str(uuid4()), "actor": "r", "role": "reviewer"}}
        ),
    )
    with TestClient(
        create_app(), headers={"Authorization": f"Bearer {token}"}
    ) as client:
        run = client.post(
            "/agent/run", json={"objective": "help", "context": {"details": "help"}}
        ).json()["run_id"]
        assert client.post(f"/agent/runs/{run}/reviews", json=review).status_code == 200
        assert (
            client.post(
                f"/agent/runs/{run}/outcomes",
                json={"event_id": str(uuid4()), "task_completed": True},
            ).status_code
            == 200
        )
        assert client.get(f"/agent/runs/{run}/evaluation").json()["review_count"] == 1
        assert (
            client.post(f"/agent/runs/{uuid4()}/reviews", json=review).status_code
            == 409
        )


def test_evaluation_cli_and_invalid_reports(tmp_path, monkeypatch):
    from app.evaluation_suite import main

    output = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["eval", "--output", str(output)])
    main()
    monkeypatch.setattr(
        "sys.argv",
        [
            "eval",
            "--output",
            str(tmp_path / "candidate.json"),
            "--baseline",
            str(output),
        ],
    )
    main()
    report = json.loads(output.read_text())
    duplicate = copy.deepcopy(report)
    duplicate["cases"].append(duplicate["cases"][0])
    with pytest.raises(ValueError):
        require_no_regression(report, duplicate)
    invalid = copy.deepcopy(report)
    invalid["cases"][0]["checks"]["action_correct"] = 1
    with pytest.raises(ValueError):
        require_no_regression(report, invalid)
    dataset = json.loads(Path("tests/fixtures/evaluation-v1.json").read_text())
    dataset["cases"][0]["expected_action"] = "incorrect"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(dataset))
    monkeypatch.setattr(
        "sys.argv", ["eval", "--dataset", str(bad), "--output", str(output)]
    )
    with pytest.raises(SystemExit):
        main()
