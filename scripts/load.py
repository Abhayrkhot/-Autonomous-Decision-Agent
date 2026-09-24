"""Bounded live-HTTP load sample; records observations, never capacity claims."""

import argparse
import asyncio
import json
import platform
import subprocess
import time
from pathlib import Path

import httpx


async def measure(url: str, count: int, concurrency: int) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    latencies = []
    statuses = {}
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:

        async def one(index):
            async with semaphore:
                started = time.perf_counter()
                response = await client.post(
                    "/agent/run",
                    json={
                        "objective": "Improve onboarding",
                        "context": {"name": str(index), "details": "Needs assistance"},
                    },
                )
                latencies.append(time.perf_counter() - started)
                statuses[str(response.status_code)] = (
                    statuses.get(str(response.status_code), 0) + 1
                )

        started = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(count)))
        elapsed = time.perf_counter() - started
    ordered = sorted(latencies)
    return {
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "environment": platform.platform(),
        "python": platform.python_version(),
        "requests": count,
        "concurrency": concurrency,
        "statuses": statuses,
        "elapsed_seconds": elapsed,
        "p50_seconds": ordered[len(ordered) // 2],
        "p95_seconds": ordered[int((len(ordered) - 1) * 0.95)],
        "raw_latency_seconds": latencies,
        "limitations": "Single local HTTP run, no warmup or production-capacity claim; includes client overhead.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.count < 1 or args.concurrency < 1:
        parser.error("count and concurrency must be positive")
    result = asyncio.run(measure(args.url, args.count, args.concurrency))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if result["statuses"] != {"200": args.count}:
        raise SystemExit("Load sample contained failures")
