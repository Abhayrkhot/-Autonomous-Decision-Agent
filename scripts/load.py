"""Closed-loop HTTP samples, including failures and reproducible raw observations."""

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import httpx

DEFAULT_PAYLOAD = {
    "objective": "Improve onboarding",
    "context": {
        "name": "Sample customer",
        "details": "Needs onboarding setup assistance",
    },
    "documents": [
        {
            "id": "setup",
            "text": "Onboarding setup assistance: complete the account checklist.",
        },
        {"id": "help", "text": "Contact onboarding support if setup is incomplete."},
        {"id": "unrelated", "text": "Garden soil and flower care."},
    ],
    "outcome_signals": {"engagement": "low"},
}


class Sample(TypedDict):
    index: int
    start_offset_seconds: float
    latency_seconds: float
    status: str


def summarize(samples: list[Sample], elapsed_seconds: float) -> dict:
    """Use inclusive linear percentiles on HTTP 200 samples only."""
    if not math.isfinite(elapsed_seconds) or elapsed_seconds <= 0:
        raise ValueError("Elapsed seconds must be finite and positive")
    statuses = Counter(sample["status"] for sample in samples)
    successful = [
        sample["latency_seconds"] for sample in samples if sample["status"] == "200"
    ]
    if len(successful) > 1:
        cuts = statistics.quantiles(successful, n=100, method="inclusive")
        p50, p95, p99 = cuts[49], cuts[94], cuts[98]
    else:
        p50 = p95 = p99 = successful[0] if successful else None
    return {
        "attempts": len(samples),
        "successful_requests": len(successful),
        "statuses": dict(sorted(statuses.items())),
        "elapsed_seconds": elapsed_seconds,
        "requests_per_second": len(samples) / elapsed_seconds,
        "successful_requests_per_second": len(successful) / elapsed_seconds,
        "p50_seconds": p50,
        "p95_seconds": p95,
        "p99_seconds": p99,
        "max_seconds": max(successful) if successful else None,
    }


def client_revision() -> dict:
    """Archives and missing Git are supported; metadata never discards a run."""
    root = Path(__file__).resolve().parent.parent
    try:
        revision = subprocess.check_output(
            ["git", "describe", "--always", "--dirty", "--abbrev=40"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=root,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        )
        return {"revision": revision, "worktree_dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"revision": None, "worktree_dirty": None}


async def sample_batch(
    client: httpx.AsyncClient, payload: dict, count: int, concurrency: int
) -> dict:
    samples: list[Sample] = []
    indices = iter(range(count))
    batch_started = time.perf_counter()

    async def worker():
        for index in indices:
            request_started = time.perf_counter()
            try:
                response = await client.post("/agent/run", json=payload)
                status = str(response.status_code)
            except httpx.HTTPError as exc:
                status = f"error:{type(exc).__name__}"
            samples.append(
                {
                    "index": index,
                    "start_offset_seconds": request_started - batch_started,
                    "latency_seconds": time.perf_counter() - request_started,
                    "status": status,
                }
            )

    await asyncio.gather(*(worker() for _ in range(min(count, concurrency))))
    elapsed = time.perf_counter() - batch_started
    return {
        "summary": summarize(samples, elapsed),
        "samples": sorted(samples, key=lambda sample: sample["index"]),
    }


async def measure(
    url: str,
    count: int,
    concurrency: int,
    *,
    timeout: float = 30,
    warmup: int = 20,
    repeat: int = 1,
    payload: dict | None = None,
    server_notes: str = "",
    server_revision: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    for name, value, minimum in (
        ("count", count, 1),
        ("concurrency", concurrency, 1),
        ("warmup", warmup, 0),
        ("repeat", repeat, 1),
    ):
        if type(value) is not int or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    request_payload = DEFAULT_PAYLOAD if payload is None else payload
    payload_json = json.dumps(request_payload, sort_keys=True)
    documents = request_payload.get("documents", [])
    result = {
        "schema_version": 2,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "client": {
            **client_revision(),
            "environment": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "server": {
            "url": url,
            "revision_reported_by_operator": server_revision,
            "notes_reported_by_operator": server_notes,
        },
        "workload": {
            "requests_per_run": count,
            "concurrency": concurrency,
            "timeout_seconds": timeout,
            "warmup_per_run": warmup,
            "repeat": repeat,
            "payload_sha256": hashlib.sha256(payload_json.encode()).hexdigest(),
            # Invalid payloads can intentionally exercise the server's 422 path.
            "document_count": len(documents) if isinstance(documents, list) else None,
        },
        "measurement": "closed-loop; excludes client-side wait before a worker starts the request",
        "percentiles": "inclusive linear interpolation; HTTP 200 requests only; null if none",
        "limitations": "Client-observed latency includes HTTP/client overhead. Operator server metadata is not independently verified. No production-capacity claim.",
        "preflight_error": None,
        "runs": [],
        "all_succeeded": False,
    }
    limits = httpx.Limits(
        max_connections=concurrency, max_keepalive_connections=concurrency
    )
    async with httpx.AsyncClient(
        base_url=url, timeout=timeout, limits=limits, transport=transport
    ) as client:
        try:
            health = await client.get("/health")
            health.raise_for_status()
        except httpx.HTTPError as exc:
            result["preflight_error"] = f"error:{type(exc).__name__}"
            return result
        for index in range(repeat):
            warmup_result = await sample_batch(
                client, request_payload, warmup, concurrency
            )
            measured = await sample_batch(client, request_payload, count, concurrency)
            result["runs"].append(
                {
                    "run": index + 1,
                    "warmup_summary": warmup_result["summary"],
                    **measured,
                }
            )
    result["all_succeeded"] = all(
        run["summary"]["successful_requests"] == count
        and run["warmup_summary"]["successful_requests"] == warmup
        for run in result["runs"]
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--server-notes", default="")
    parser.add_argument("--server-revision")
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = None
    if args.payload:
        try:
            payload = json.loads(args.payload.read_text())
        except (OSError, ValueError) as exc:
            parser.error(f"Cannot read payload JSON: {type(exc).__name__}")
        if not isinstance(payload, dict):
            parser.error("Payload must be a JSON object")
    try:
        result = asyncio.run(
            measure(
                args.url,
                args.count,
                args.concurrency,
                timeout=args.timeout,
                warmup=args.warmup,
                repeat=args.repeat,
                payload=payload,
                server_notes=args.server_notes,
                server_revision=args.server_revision,
            )
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if not result["all_succeeded"]:
        print("Load sample contained failures; observations were saved.")
        return 1
    return 0


if (
    __name__ == "__main__"
):  # pragma: no cover -- thin CLI wrapper; main is tested directly.
    raise SystemExit(main())
