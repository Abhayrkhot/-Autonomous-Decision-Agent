# Stage 1 verification

A clean archive of revision 518c1bb passed 70 tests with 100% application statement
and branch coverage on Python 3.11.9/macOS ARM64. The suite includes Hypothesis
properties, planner truth tables, injection patterns across all text fields,
malformed API requests, storage/tool failure injection, and 300 ASGI requests.

`stage-1-load.json` contains a separate live HTTP sample: 1,000 requests, concurrency
20, raw client latency samples and environment metadata. This is bounded local load,
not an endurance test or a production-capacity estimate. No RSS measurement or
external-service behavior is claimed. The upstream TestClient deprecation warning
is recorded in the verification log. CI supplies additional Python-version checks.
