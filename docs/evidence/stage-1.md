# Stage 1 verification after PR review

Tested source revision: `cd993b1c989ea4e17a88ee6353d5122def9f6b4c`.
The following evidence was collected from a fresh local clone of that revision,
with a fresh virtual environment installed from `requirements-dev.txt`. Later
evidence-only commits do not change the measured application or test code.

## Verification gate

`bash scripts/verify.sh` passed dependency consistency, lint, formatting, and
**173 pytest cases with 100% application and load-reporter statement/branch
coverage** on Python 3.13.4 / macOS ARM64. See `stage-1-verification.txt` for the
unabridged gate log and `stage-1-environment.txt` for installed package versions.
The minimum enforced coverage is also 100%; the thin CLI entry wrapper is excluded
with a reason, while `main()` is tested directly. Parameter combinations contribute
to the test count and are not independent bug discoveries.

The suite covers planner outcomes, strict nested contracts, retrieval ranking and
rounded-zero regression, tokenizer/retrieval properties, accepted and rejected
guardrail inputs, known false positives, maximum fields, document-count limits,
tool/store failures, nested read/write storage copies, and eviction/overwrite order.
The 300-request ASGI test deliberately yields inside a tool to create interleaving.
A negative control injects shared request state and proves the same assertions fail.
The load reporter's arithmetic, zero/single-success cases, failure recording,
warmup/repeats, metadata, client configuration and CLI are also tested.

The upstream TestClient/AnyIO deprecation warning remains visible in the log.
CI provides additional Python 3.11–3.13 checks. Execution coverage does not prove
correctness or security.

## Live HTTP observations

`stage-1-load.json` was generated at `2026-09-24T06:11:55.685182+00:00` from the
same clean source revision. It contains three runs of 1,000 measured requests each,
at concurrency 20, with 50 separate warmups per run. All 3,000 measured and 150
warmup requests returned HTTP 200. The committed default payload has three
documents and exercises retrieval.

The operator started Uvicorn from that checkout, on the same Mac as the client,
with one worker, reload disabled and access logging disabled. The OS assigned
port 60750. The complete server invocation, Python/platform/CPU count, source
revision, clean status, payload hash, raw indexed measurements and run summaries
are in the JSON. The server revision is supplied by the operator, not attested
through `/health`. The temporary server was stopped after measurement.

For reproduction, use the README load command against the tested revision and
retain the default payload. Timings will vary with host and background activity.
P50/P95/P99 use inclusive linear interpolation on HTTP 200 responses only;
warmup summaries are separate, and HTTP failures would remain in the report.

This is bounded **closed-loop local load**, excluding client waiting before a
worker starts a request and including client/HTTP overhead. It is not an open-loop
arrival test, endurance test, maximum-capacity result, RSS measurement, or an
external-service exercise. Three short runs do not establish long-term stability.

SHA-256 of `stage-1-load.json`:
`d6a8e9f754508976c26cf06c277e00eb04738390c874dae138f937c0b10ba23f`.
