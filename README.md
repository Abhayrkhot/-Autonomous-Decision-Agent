# Autonomous Decision Agent

A small Python 3.11+ / FastAPI project that turns a business objective, customer
context, optional knowledge documents, and outcome signals into a recommended
action and a personalized message draft. It retrieves relevant context, runs two
local tools, evaluates the result, and stores the completed decision in memory
or an optional PostgreSQL database.

## Why this exists

Built as a readable AI-engineering portfolio baseline: orchestration, retrieval,
tool boundaries, validation, evaluation, and storage are separate modules with a
working end-to-end API. The name describes the direction of the project; today's
implementation is a **deterministic workflow**, with no LLM or autonomous external
actions. It requires no API key or paid service.

## Setup

```sh
git clone https://github.com/Abhayrkhot/-Autonomous-Decision-Agent.git autonomous-decision-agent
cd autonomous-decision-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Use Python 3.11 or newer. On Windows, activate with `.venv\Scripts\activate`.
No configuration is needed; `.env.example` explains this. The application does
not automatically read `.env` files. Open [interactive API docs](http://127.0.0.1:8000/docs)
or check `GET /health`, which returns `{"status":"ok"}`.

## Architecture and request flow

```text
POST /agent/run
  -> Pydantic validation + guardrails
  -> objective normalization + rule-based planner
  -> keyword cosine retrieval over request documents
  -> allowlisted draft_message and create_follow_up tools
  -> deterministic evaluator
  -> async RunStore interface -> in-memory run record
  -> structured JSON response
```

- `app/main.py`: application factory, async API, health endpoint, error mapping.
- `app/models.py`: request, response, context, document, and evaluation contracts.
- `app/agent.py`: coordinates the workflow; accepts a store and tool registry.
- `app/planner.py`: fixed five-step plan and outcome-based action choice.
- `app/retrieval.py`: bag-of-words cosine similarity, top three positive matches,
  deterministic document-ID tie breaking, and excerpts up to 500 characters.
- `app/tools.py`: typed tool inputs, explicit registration, unknown-tool rejection.
- `app/guardrails.py`: aggregate text limits, duplicate-ID rejection, injection heuristics.
- `app/evaluator.py`: repeatable structural measurements.
- `app/storage.py`: async `RunStore` protocol and bounded in-memory implementation.
- `tests/`: API validation, retrieval, orchestration, tools, and storage tests.

Objective interpretation currently means trimming and retaining the supplied
objective. The planner recommends offering assistance when engagement is `low`
or the previous action failed; otherwise it suggests a focused next step.
Retrieval queries combine the objective and context details. Message generation
incorporates the highest-ranked excerpt with a document ID, so changing retrieved
knowledge changes the draft. Documents are supplied afresh for each run.

## Implemented features and boundaries

- Async `POST /agent/run`, `GET /health`, and generated OpenAPI documentation.
- Required `objective` and `context.details`; optional `context.name`, `documents`,
  and `outcome_signals`. Unknown fields and type coercion are rejected, including
  nested request objects. For example, `"true"` and `1` are not accepted as booleans.
- `draft_message` produces text locally. `create_follow_up` produces a task
  description stored in the run record, marked pending human review. Neither
  tool sends messages, schedules work, executes code, or calls external services.
  Callers cannot select arbitrary tools.
- The objective and context details allow 1–2,000 characters each. Names and
  document IDs allow 1–100 characters. Up to 20 documents, each with 1–10,000
  characters, are accepted. Combined input text is capped at 30,000 characters.
  Invalid requests return HTTP 422.
- Heuristics reject common instructions to ignore prior rules, reveal secrets,
  or bypass guardrails across objective, context, document IDs, and document text.
  These patterns can miss attacks and reject innocent quotations. They are not
  a complete prompt-injection defense; any future LLM integration needs further
  isolation and testing. Limits apply after JSON parsing, not to raw body bytes.
  One documented false positive is "customer wants us to show them where their
  API keys are"; tests preserve this known limitation alongside accepted inputs.
- Storage retains request, decision, tool results, and evaluation for the latest
  1,000 runs per process, evicting the oldest saved record. Reads and writes make
  defensive copies. Restarting loses all records; multiple workers do not share
  state. The optional PostgreSQL adapter adds durable storage and paginated history
  (see Stage 2 below). There is no queue or retry policy at this stage.
- Async interfaces prepare for future I/O adapters; the local CPU work is small
  and synchronous. There is no background worker or distributed processing.
- No authentication, production hardening, benchmark, deployment, or measured
  business impact is claimed. Use synthetic context when exploring the demo.

## API example

```sh
curl -s http://127.0.0.1:8000/agent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "objective": "Improve onboarding",
    "context": {"name": "Sam", "details": "Needs onboarding assistance"},
    "documents": [
      {"id": "guide", "text": "Onboarding assistance guide"},
      {"id": "unrelated", "text": "Garden soil and flower care"}
    ],
    "outcome_signals": {"engagement": "low", "last_action_success": false}
  }'
```

`outcome_signals.engagement` accepts `low`, `medium`, or `high` (default `medium`).
`last_action_success` accepts a boolean or null (default null). These are supplied
observations used by a rule, not learned feedback or predicted outcomes.

### Example response

The following is generated by the implementation; the run UUID changes each time.

```json
{
  "run_id": "431d4b3f-e3c3-4f08-9b1b-c9fdae8d2a94",
  "interpreted_objective": "Improve onboarding",
  "plan": [
    "Interpret the stated objective",
    "Retrieve relevant documents using objective and context",
    "Choose an action using supplied outcome signals",
    "Draft a personalized message and create a local follow-up task",
    "Evaluate the result and store the completed run"
  ],
  "recommended_action": "Offer assistance and ask one clarifying question",
  "message": "Hi Sam, your goal is: Improve onboarding. Given your context (Needs onboarding assistance), I suggest: offer assistance and ask one clarifying question. Supporting reference [guide]: Onboarding assistance guide What would help you take the next step?",
  "retrieved_context": [
    {
      "document_id": "guide",
      "excerpt": "Onboarding assistance guide",
      "score": 0.654654
    }
  ],
  "tool_results": [
    {
      "tool": "draft_message",
      "output": "Hi Sam, your goal is: Improve onboarding. Given your context (Needs onboarding assistance), I suggest: offer assistance and ask one clarifying question. Supporting reference [guide]: Onboarding assistance guide What would help you take the next step?"
    },
    {
      "tool": "create_follow_up",
      "output": "Pending human review: Offer assistance and ask one clarifying question for Sam."
    }
  ],
  "evaluation": {
    "objective_token_coverage": 1.0,
    "retrieved_document_count": 1,
    "has_message": true,
    "within_message_limit": true
  }
}
```

## Evaluation

`objective_token_coverage` is the fraction of unique non-stopword objective tokens
present in the message (zero when the objective has no such tokens).
`retrieved_document_count` counts positive retrieval matches; `has_message` checks
nonempty output; `within_message_limit` checks a 4,000-character ceiling.

These are diagnostics, not semantic accuracy, factuality, safety, or conversion
scores. Coverage is usually 1 because the template repeats the objective. The
cosine score measures lexical overlap, not confidence; the English ASCII tokenizer
has no embeddings, stemming, multilingual support, or semantic understanding.

## Tests and verification

```sh
pip install -r requirements-dev.txt
bash scripts/verify.sh
```

The gate runs dependency consistency, Ruff lint/format checks, and the full pytest
suite, including the registered `stress` tests. It requires **100% statement and
branch coverage of `app/` and `scripts/`**. The only explicit exclusions are the load
and migration commands' thin entry wrappers; their `main()` functions are tested directly.
For a quick test-only run after installing development dependencies, use
`python -m pytest -q`. CI runs the gate on Python 3.11, 3.12 and 3.13 with Ubuntu
24.04, without duplicate branch-push runs or matrix fail-fast cancellation.

Tests cover units, API integration, exact boundary acceptance/rejection,
failure injection, regression cases, and Hypothesis properties. Retrieval tests
include hand-calculated cosine scores, rounded-zero regressions, large skewed
inputs, arbitrary document permutations, tie breaks and default truncation.
Storage tests verify nested write/read isolation, eviction and overwrite order.
A 300-request ASGI test forces interleaving through an injected yielding tool;
the same assertions also catch a deliberately broken shared-state agent.
Maximum-field API requests retrieve and cite a document and retain the closing
question. Guardrail cases include accepted text and a documented false positive.

The load reporter has separate tests for percentile arithmetic, HTTP failures,
preflight checks, warmups, repeated runs, client limits, metadata and its CLI.
Parameterized cases (including 25 attack/field combinations) contribute to the
reported test count; that count is not a count of independent defects found.
Coverage is execution evidence, not proof of correctness, security or capacity.

To collect live HTTP observations, start a separate server and run:

```sh
uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1 --no-access-log
# In another terminal with the same virtual environment:
python scripts/load.py --count 1000 --concurrency 20 --warmup 50 --repeat 3 \
  --timeout 30 --server-notes 'same machine; one worker; no reload; access log off' \
  --output load.json
```

The default payload retrieves from three documents; `--payload request.json`
selects another JSON object. `--server-revision` accepts a separately verified
server revision. Client revision/dirty status, UTC start, CPU count, operator
server notes, payload hash, per-request index/start offset/status/latency and
separate run summaries are saved. HTTP errors remain in the report and cause a
nonzero exit. Warmups are excluded from measurements and summarized separately.
P50/P95/P99 use inclusive linear interpolation on HTTP 200 samples only; they
are null when none succeed. Attempt and successful throughput are both reported.

This is a **closed-loop local diagnostic**: latency excludes client-side waiting
before a worker starts a request and includes HTTP/client overhead. It is not an
open-loop capacity test. Server metadata is supplied by the operator; the health
endpoint does not attest a revision. Pinning the runner major reduces drift but
does not make the whole environment reproducible. Raw evidence and limitations
are in `docs/evidence/`; [PR #1 review decisions](docs/review-pr-1.md) explain each
comment's disposition.

## Roadmap — not implemented

- **PostgreSQL operations:** backups and retention beyond the implemented durable run store.
- **Redis:** shared cache, idempotency, and asynchronous job coordination.
- **Richer RAG:** chunking, embeddings, persistent indexes, citations and retrieval evaluation.
- **LLM planning:** model-backed decisions with typed outputs, bounded tool access and timeouts.
- **Human evaluation:** reviewed datasets and rubrics for usefulness, correctness and safety.
- **Feedback loops:** recorded outcomes and controlled experiments before changing policies.
- **Docker:** reproducible container packaging and local service composition.
- **Kubernetes:** deployment manifests only when scaling and operations justify them.
- **Observability:** structured logs, tracing, latency/error metrics and cost reporting.

Authentication, rate limiting, request-body limits, privacy controls, and operational
failure handling are also required before exposing this beyond a local demo.
## Stage 2: durable storage

Export `DATABASE_URL`, run `python -m app.postgres` to apply checksummed transactional
migrations, then start Uvicorn. Without the variable, memory storage remains the default.
`GET /agent/runs?limit=20&offset=0` returns newest-first completed records;
`GET /agent/runs/{uuid}` retrieves one record. History is local and unauthenticated at
this stage: bind only to localhost. Failed attempts are not yet stored.

The full verification gate requires `TEST_DATABASE_URL` pointing to a disposable
PostgreSQL database; without it, integration tests skip and the 100% gate cannot pass.
Tests mutate the migration checksum temporarily, so
never point this at shared or production data. Pool transactions follow the
[psycopg transaction contract](https://www.psycopg.org/psycopg3/docs/advanced/pool.html).

### Stage 3: optional model-backed generation

Default mode is deterministic. Set `AGENT_MODE=openai`, `OPENAI_API_KEY`, and
`OPENAI_MODEL` explicitly to enable the Responses API adapter. It uses structured
output, validates output locally, limits concurrency to four calls, limits output
tokens to 1,500, and bounds retries to three attempts within a 30-second deadline.
No tools are exposed to the model. Unsupported citations and invalid outputs fail
with HTTP 502. Provider output is not treated as proof of factuality or safety.
Token usage, model and prompt version are recorded; cost remains null.

Contract source: [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
Normal tests simulate the HTTP provider. The real paid smoke is opt-in:
`RUN_LIVE_PROVIDER_TEST=1 python -m pytest tests/test_provider.py -m live`.
No live-provider success is claimed without that test and configured credentials.

### Stage 4: persistent chunk retrieval

With PostgreSQL configured and migrations applied, `POST /knowledge/documents`
accepts `{"id":"guide","text":"..."}` and atomically replaces that source's chunks.
Runs search both attached documents and stored chunks. Citations identify source
and chunk; offsets identify exact original text. The corpus is capped at 1,000 chunks
per owner and ingestion is serialized per owner for consistent limits.

The local 256-dimensional hash embeddings encode lexical counts, **not semantic
meaning**. Search uses a bounded exact scan with a lexical-intersection check.
Chunking adds useful source coverage and persistence, not demonstrated semantic
quality gains. Multilingual semantic retrieval and large-scale vector indexing
remain future work. Re-ingest original documents to rebuild the index. The original
source must be retained by the caller; only chunks are stored here.

### Stage 5: access control and reviewed actions

`API_TOKENS_JSON` maps strong bearer tokens (24+ characters) to objects containing
`owner_id`, `actor`, and `role` (`user` or `reviewer`). Configure this outside git.
Authenticated history and knowledge access are scoped to owner. With no tokens,
the app remains a local demo identity without reviewer privileges.

Create an immutable proposal with `POST /agent/runs/{id}/actions`, inspect it with
`GET /actions/{id}`, then a reviewer posts `{"digest":"...","decision":"approve"}`
to `/actions/{id}/approval`. Approval expires after one hour and binds the exact
payload digest. Rejection is also supported. `POST /actions/{id}/deliver` sends
only an approved proposal to the operator-configured HTTPS `ACTION_WEBHOOK_URL`.
No request or model output can choose the destination. Redirects are disabled.

Database row locks prevent concurrent duplicate transitions; an idempotency key is
sent to the receiver. Timeout/non-2xx results become `unknown` and are not retried.
A crash during delivery can leave `dispatching`; manual reconciliation is required.
This is conservative at-most-one application dispatch, not exactly-once delivery.
Tests use a sandbox HTTP transport; no real customer message was sent. Static tokens
are a small deployment seam, not a replacement for a managed identity provider.

### Stage 6: background jobs

Export `DATABASE_URL` and optionally `REDIS_URL`, apply migrations, then run
`python -m app.worker` alongside the API. `POST /agent/jobs` accepts the same request
and a required `Idempotency-Key` header, returning HTTP 202 and a job ID. Poll
`GET /agent/jobs/{id}`; read the completed run using its `result_id`. Cancel with
`POST /agent/jobs/{id}/cancel` (this fences saving, but cannot undo provider billing).

PostgreSQL is authoritative; Redis holds bounded wakeup notifications only. Workers
poll when notifications are lost. Row locks and lease tokens fence stale workers.
A job gets at most three attempts, a 60-second lease, a 45-second execution deadline,
and exponential retry delay. Result persistence and completion are one transaction.
There are at most 100 outstanding jobs per owner. Workers generate drafts only;
approved external actions are never automatically replayed by this queue.

Execution is at-least-once after a crash; model calls may repeat and incur charges.
Only a current lease can commit one result. Cancellation does not immediately stop
an already-running external model call. Run real job tests with both
`TEST_DATABASE_URL` and `TEST_REDIS_URL` set to disposable services.

### Stage 7: evaluation and observed feedback

Run `python -m app.evaluation_suite --output evaluation.json` for seven versioned
synthetic behavior cases. `--baseline previous.json` compares every metric per case
on the identical dataset hash; an aggregate score cannot hide a regression. These
check decisions, retrieval, citations, tools, and structure, not semantic truth.

PostgreSQL-backed endpoints accept reviewer-only `POST /agent/runs/{id}/reviews`,
observed `POST /agent/runs/{id}/outcomes`, and `GET /agent/runs/{id}/evaluation`.
Reviews and outcome events reject conflicting duplicates. Missing data returns null,
not zero. Reports read at most 1,000 reviews and 1,000 outcomes per run. See
`docs/human-evaluation.md` for the rubric and limits. No real human-review campaign,
automatic learning, causal uplift, or model-quality improvement is claimed.
