# Final verification: Stages 2 to 8 integrated

Revision `94a5bf0` (clean tree), collected 2026-11-22 on Linux x86_64 with Docker
Compose 2.40.3. Logs in this directory are unabridged except that image build and
container lifecycle lines were dropped from `final-e2e-run.txt`.

## Gate

`scripts/dev.sh bash scripts/verify.sh -rs` against PostgreSQL 17 and Redis 7
containers, once per Python version:

| Python | Result | Coverage (statement and branch) | Log |
| --- | --- | --- | --- |
| 3.11.18 | 222 passed, 1 skipped | 100% | `final-verify-py3.11.txt` |
| 3.12.16 | 222 passed, 1 skipped | 100% | `final-verify-py3.12.txt` |
| 3.13.18 | 222 passed, 1 skipped | 100% | `final-verify-py3.13.txt` |

The skip is the paid live model test, which needs `RUN_LIVE_PROVIDER_TEST=1` and a
real key. Each run also passed `pip check`, Ruff lint and format checks, and the
behavior evaluation suite. One upstream deprecation warning remains (Starlette's
test client suggests `httpx2`).

## End to end

`e2e/run.sh` built the image and ran the compose stack: 67 of 67 checks passed
(`final-e2e-report.json`, `final-e2e-run.txt`). Phases: core 48, queued 2, drain 2,
recovery 4, model 9, and two worker lifecycle checks (SIGTERM exit 0 after 0.66 s and
0.76 s). In the deterministic phase the API logged 35 responses of 200, 4 of 202 and
only expected client errors (401, 403, 404, 409, 413, 422), with no 5xx before or
after the PostgreSQL restart. The model phase logged one 502, the deliberate
provider outage.

The model endpoint was a local stub (`e2e/stubs.py`), so this verifies wiring,
validation and failure handling, not model quality.

## Dependencies

`dependency-audit.json`: `pip-audit --strict --require-hashes` over
`requirements.lock` (the exact set the image installs) found 0 known
vulnerabilities in 21 packages.

## Bugs found and fixed during integration

- Python 3.11 can lose a task cancellation inside `asyncio.wait_for`. Under heavy
  host load a worker test hung because the worker survived `cancel()`. The worker now
  stops through an event set by SIGTERM or SIGINT; without that, a worker running as
  PID 1 in a container would also have ignored `docker stop` until killed.
- The provider sent Pydantic's generated schema in strict structured-output mode,
  including length keywords outside the strict subset. It now sends an explicit
  schema and enforces lengths locally. The endpoint is configurable.
- Not-found and wrong-role errors from jobs, actions and feedback returned 409; they
  now return 404 and 403 like the rest of the API.
- After a PostgreSQL restart the pool could hand out dead connections; it now
  validates them on checkout.
- `compose up --wait` could not start because the worker had no health check; it now
  writes a heartbeat that the health check reads.
