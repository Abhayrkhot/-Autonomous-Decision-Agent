"""Versioned deterministic behavior evaluations; no semantic-quality claims."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.agent import DecisionAgent
from app.models import AgentRequest
from app.provider import DecisionProvider
from app.storage import InMemoryRunStore


async def run_suite(path: Path, provider: DecisionProvider | None = None) -> dict:
    dataset = json.loads(path.read_text())
    cases = dataset["cases"]
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Evaluation cases must have unique IDs and be nonempty")
    agent = DecisionAgent(InMemoryRunStore(), provider=provider)
    results = []
    for case in cases:
        response = await agent.run(AgentRequest.model_validate(case["request"]))
        retrieved = {item.document_id for item in response.retrieved_context}
        checks = {
            "action_correct": response.recommended_action == case["expected_action"],
            "retrieval_correct": retrieved == set(case["expected_sources"]),
            "citation_valid": set(response.citations) <= retrieved,
            "tool_sequence_correct": [tool.tool for tool in response.tool_results]
            == case["expected_tools"],
            "format_complete": bool(response.plan and response.message)
            and response.evaluation.within_message_limit,
        }
        results.append(
            {"id": case["id"], "checks": checks, "passed": all(checks.values())}
        )
    return {
        "dataset_version": dataset["version"],
        "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mode": "provider" if provider else "deterministic",
        "cases": results,
        "all_passed": all(case["passed"] for case in results),
    }


def require_no_regression(baseline: dict, candidate: dict) -> None:
    if baseline["dataset_sha256"] != candidate["dataset_sha256"]:
        raise ValueError("Dataset changed; comparisons require an identical dataset")
    for report in (baseline, candidate):
        if len({case["id"] for case in report["cases"]}) != len(report["cases"]):
            raise ValueError("Duplicate evaluation case")
        if any(
            type(value) is not bool
            for case in report["cases"]
            for value in case["checks"].values()
        ):
            raise ValueError("Metrics must be booleans")
    before = {case["id"]: case for case in baseline["cases"]}
    after = {case["id"]: case for case in candidate["cases"]}
    if before.keys() != after.keys():
        raise ValueError("Cases changed")
    for case_id, old in before.items():
        new = after[case_id]
        if old["checks"].keys() != new["checks"].keys():
            raise ValueError("Metrics changed")
        if any(
            value is True and new["checks"][name] is not True
            for name, value in old["checks"].items()
        ):
            raise ValueError(f"Regression in case {case_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", type=Path, default=Path("tests/fixtures/evaluation-v1.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run_suite(args.dataset))
    if args.baseline:
        require_no_regression(json.loads(args.baseline.read_text()), report)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if not report["all_passed"]:
        raise SystemExit("Evaluation failed")


if __name__ == "__main__":
    main()
