# Autonomous Decision Agent

A FastAPI service that turns a business objective, customer context, knowledge
documents and observed outcome signals into a recommended action and a drafted
customer message, then carries that decision through the parts a real system
needs: durable history, asynchronous jobs, human approval before anything leaves
the system, audited delivery, and feedback on how the decision worked out.

Decisions are made by a deterministic rule-based planner by default, so the whole
system runs without an API key. An optional model mode sends the same inputs to any
OpenAI Responses API compatible endpoint and validates what comes back. Nothing is
sent to a customer automatically: an outbound action needs a reviewer's approval of
its exact content first.

## Verified status

All numbers below come from the committed evidence in [`docs/evidence/`](docs/evidence)
for revision `94a5bf0`.

| Check | Result |
| --- | --- |
| Unit and integration gate (`scripts/verify.sh`) against real PostgreSQL 17 and Redis 7 | 222 passed, 1 skipped (paid live model call), on Python 3.11, 3.12 and 3.13 |
| Statement and branch coverage of `app/` and `scripts/` | 100% on all three versions (enforced) |
| Behavior evaluation suite (`app.evaluation_suite`, 7 versioned cases) | all pass |
| End-to-end run of the Docker Compose stack (`e2e/run.sh`) | 67 of 67 checks passed |
| Worker shutdown on SIGTERM | exit code 0 after 0.66 s and 0.76 s (grace period 30 s) |
| `pip-audit` of the hash-locked runtime set | 21 packages, 0 known vulnerabilities |

CI runs the gate on all three Python versions, the dependency audit, and the full
end-to-end run on every pull request.

## Architecture

```text
             +--------------------------- API (FastAPI, uvicorn) ----------------------------+
 client ---> | OperationalMiddleware: body limit, per-client rate limit, request id, JSON log |
  bearer     | auth -> owner-scoped principal (user | reviewer)                               |
  token      |                                                                               |
             |  POST /agent/run ---------> DecisionAgent ----------------> RunStore -------+ |
             |  POST /agent/jobs --+         guardrails, planner,            (PostgreSQL)  | |
             |                     |         retrieval (request docs +                     | |
             |                     |         persistent chunks), tools or                  | |
             |                     |         model provider, evaluator                     | |
             |  actions, reviews, outcomes, history, knowledge ingest ---------------------+ |
             +---------------------|-----------------------------------------------------------+
                                   | insert job (PostgreSQL is the source of truth)
                                   | LPUSH wake-up (Redis is only a hint)
                                   v
             worker processes: lease a job (FOR UPDATE SKIP LOCKED) -> DecisionAgent -> commit
             result only while holding the current lease; SIGTERM finishes the current job

 approved action --(one attempt, HTTPS, Idempotency-Key)--> configured webhook
```

| Module | Responsibility |
| --- | --- |
| `app/main.py` | Application factory, routes, error mapping, readiness and metrics |
| `app/models.py` | Strict request contracts (unknown fields and type coercion rejected) and response models |
| `app/agent.py` | Orchestrates one decision: validate, plan, retrieve, generate, evaluate, persist |
| `app/planner.py` | Fixed five-step plan and the outcome-signal decision rule |
| `app/retrieval.py` | Per-request bag-of-words cosine retrieval over supplied documents |
| `app/knowledge.py` | Persistent owner-scoped chunks with offsets and lexical hash embeddings |
| `app/provider.py` | Optional Responses API provider: bounded retries, deadline, strict output validation |
| `app/tools.py` | Allowlisted local tools: `draft_message`, `create_follow_up` |
| `app/guardrails.py` | Aggregate size limits, duplicate IDs, prompt-injection heuristics |
| `app/storage.py`, `app/postgres.py` | `RunStore` protocol, in-memory and PostgreSQL adapters, checksummed migrations |
| `app/jobs.py`, `app/worker.py` | Durable job queue with idempotency, leases and fencing; worker process |
| `app/actions.py` | Immutable action proposals, digest-bound approval, audited single-attempt delivery |
| `app/feedback.py`, `app/evaluation_suite.py` | Human reviews, observed outcomes, versioned behavior evaluation |
| `app/operations.py`, `app/auth.py`, `app/errors.py` | Request controls, bearer-token principals, HTTP status mapping |

## Quick start

### Local, in memory (no services needed)

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Without `DATABASE_URL` the service keeps the latest 1,000 runs in process memory
and the history and decision endpoints work. Endpoints that need durable state
(knowledge, jobs, actions, feedback) return 503. Without `API_TOKENS_JSON` every
caller is treated as a local user; use this mode only on your own machine.
Interactive docs are at `http://127.0.0.1:8000/docs`.

### Full stack with Docker Compose

```sh
export POSTGRES_PASSWORD=$(openssl rand -hex 16)
USER_TOKEN=$(openssl rand -hex 24)
REVIEWER_TOKEN=$(openssl rand -hex 24)
export API_TOKENS_JSON="{\"$USER_TOKEN\":{\"owner_id\":\"acme\",\"actor\":\"alice\"},\"$REVIEWER_TOKEN\":{\"owner_id\":\"acme\",\"actor\":\"rita\",\"role\":\"reviewer\"}}"
docker compose up --build -d --wait
```

This starts PostgreSQL, Redis, a one-shot migration container, the API on
`127.0.0.1:8765` and one worker. The API and worker run as a non-root user on a
read-only filesystem with all capabilities dropped. Compose sets
`DEPLOYMENT_MODE=staging`, which refuses to start without configured tokens.

```sh
auth="Authorization: Bearer $USER_TOKEN"
curl -s localhost:8765/knowledge/documents -H "$auth" -H 'Content-Type: application/json' \
  -d '{"id":"playbook","text":"Customers who stall during workspace setup usually miss the data import step. A guided onboarding session fixes it."}'
curl -s localhost:8765/agent/run -H "$auth" -H 'Content-Type: application/json' \
  -d '{"objective":"Improve onboarding completion","context":{"name":"Sam","details":"Stalled during data import"},"outcome_signals":{"engagement":"low"}}'
curl -s localhost:8765/agent/jobs -H "$auth" -H 'Idempotency-Key: first-job' -H 'Content-Type: application/json' \
  -d '{"objective":"Improve onboarding completion","context":{"details":"Stalled during data import"}}'
```

Add workers with `docker compose up -d --scale worker=3`. Stop everything with
`docker compose down` (add `-v` to delete the data volumes).

## API

All endpoints except `/health` and `/ready` take `Authorization: Bearer <token>` when
tokens are configured. Every resource is scoped to the caller's `owner_id`; another
owner's run, job or action is reported as not found.

| Method and path | Role | Purpose |
| --- | --- | --- |
| `POST /agent/run` | user | Make and store a decision synchronously |
| `GET /agent/runs?limit=&offset=` | user | Owner's run history, newest first |
| `GET /agent/runs/{run_id}` | user | One stored run with its request, decision and evaluation |
| `POST /knowledge/documents` | user | Add or replace a persistent knowledge document (chunked) |
| `POST /agent/jobs` | user | Queue a decision; needs an `Idempotency-Key` header; returns 202 |
| `GET /agent/jobs/{job_id}` | user | Job status, attempts and result run ID |
| `POST /agent/jobs/{job_id}/cancel` | user | Cancel a queued or running job |
| `POST /agent/runs/{run_id}/actions` | user | Propose sending the run's message; returns the content digest |
| `GET /actions/{action_id}` | user | Action status |
| `POST /actions/{action_id}/approval` | reviewer | Approve or reject, quoting the exact digest |
| `POST /actions/{action_id}/deliver` | user | Deliver an approved action once to the configured webhook |
| `POST /agent/runs/{run_id}/reviews` | reviewer | Immutable rubric review (usefulness, correct, grounded, safe) |
| `POST /agent/runs/{run_id}/outcomes` | user | Idempotent observed outcome, optionally linked to an action |
| `GET /agent/runs/{run_id}/evaluation` | user | Aggregated reviews and outcomes for the run |
| `GET /health`, `GET /ready` | none | Liveness; readiness checks the database and migrations |
| `GET /metrics` | reviewer | Request count, error count and total duration for this process |

Status codes are consistent across endpoints: 401 missing or unknown token, 403
wrong role, 404 not found for this owner, 409 state conflict (wrong digest, action
not in the expected state, idempotency key reused with a different body, conflicting
review or outcome), 413 body too large, 422 invalid input (validation errors list
field locations and types but never echo the submitted values), 429 rate limit or
too many outstanding jobs, 502 model provider failure, 503 dependency not configured
or database unavailable.

`outcome_signals.engagement` is `low`, `medium` or `high` (default `medium`) and
`last_action_success` is a boolean or null. The rule offers assistance and asks a
clarifying question when engagement is low or the last action failed; otherwise it
suggests a focused next step. These are supplied observations, not predictions.

### Example response

Generated by the current implementation (in-memory mode, inline documents):

```json
{
  "run_id": "849ab9b4-fa57-4bca-9e61-f255bc04c04a",
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
      "source_id": null,
      "start_offset": null,
      "end_offset": null,
      "document_id": "guide",
      "excerpt": "Onboarding assistance guide",
      "score": 0.654654
    }
  ],
  "tool_results": [
    {"tool": "draft_message", "output": "Hi Sam, your goal is: Improve onboarding. Given your context (Needs onboarding assistance), I suggest: offer assistance and ask one clarifying question. Supporting reference [guide]: Onboarding assistance guide What would help you take the next step?"},
    {"tool": "create_follow_up", "output": "Pending human review: Offer assistance and ask one clarifying question for Sam."}
  ],
  "evaluation": {
    "objective_token_coverage": 1.0,
    "retrieved_document_count": 1,
    "has_message": true,
    "within_message_limit": true
  },
  "generation": null,
  "citations": ["guide"]
}
```

Request: objective `Improve onboarding`, context `{"name": "Sam", "details": "Needs
onboarding assistance"}`, two documents (`guide`, `unrelated`) and low engagement.
Persistent knowledge results carry `source_id` plus character offsets into the
original document, and their `document_id` is `<source_id>#<chunk>`. In model mode
`tool_results[0].tool` is `model_draft` and `generation` records the model, prompt
version and token counts (`cost_usd` stays null: no price schedule is assumed).

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | unset (in memory) | PostgreSQL DSN; enables history, knowledge, jobs, actions and feedback |
| `REDIS_URL` | unset | Wake-up notifications for workers; without it workers poll |
| `API_TOKENS_JSON` | unset (single local user) | `{"<token, 24+ chars>": {"owner_id": ..., "actor": ..., "role": "user" or "reviewer"}}` |
| `DEPLOYMENT_MODE` | `local` | `staging` refuses to start without `API_TOKENS_JSON` |
| `AGENT_MODE` | `deterministic` | `openai` uses the model provider for plan, action and message |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | unset | Required in `openai` mode |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any Responses API compatible endpoint |
| `ACTION_WEBHOOK_URL` | unset | HTTPS endpoint for approved actions; delivery returns 409 without it |
| `MAX_BODY_BYTES` | `262144` | Request body limit, enforced before parsing |
| `RATE_LIMIT_PER_MINUTE` | `1000` | Per client address, per API process |
| `WORKER_HEARTBEAT_FILE` | `/tmp/ada-worker-heartbeat` | Touched every worker loop; used by the compose health check |

Migrations run with `python -m app.postgres` (the compose `migrate` service does
this). They are plain SQL files applied in order under an advisory lock, and a
changed checksum for an applied file stops startup. `.env.example` lists the
variables; the application does not read `.env` files itself.

## How the pieces behave

**Retrieval.** Each request combines up to three matches from documents supplied
with the request (bag-of-words cosine) and from the owner's persistent knowledge.
Ingested documents are split into 500-character chunks with 80 characters of overlap
and embedded with deterministic lexical hash vectors; a result also needs a shared
token, so hash collisions alone never become evidence. Replacing a document is atomic,
and a per-owner lock enforces a 1,000-chunk cap. This is lexical matching, not
semantic search.

**Model mode.** The provider sends `store: false`, no tools, an instruction to treat
context and documents as untrusted data, and a strict JSON schema limited to the
keywords structured outputs accept. Length limits, allowed citations (only IDs that
were retrieved) and a single completed output are checked locally. Calls are limited
to four in flight per process, make at most three attempts on 429, 5xx and transport
errors, and are bounded by a 30 second deadline. Failures return 502 for synchronous runs
and a stable `execution_failed` code for jobs, without leaking provider details.

**Jobs.** PostgreSQL holds every job; Redis only wakes workers sooner, so a lost
notification delays work but never loses it. Submissions are idempotent per owner
and key (same key with a different body is a 409) and limited to 100 outstanding
jobs per owner. A worker leases one job with `FOR UPDATE SKIP LOCKED`, runs it with a
45 second timeout, and commits the result only if it still holds the current lease,
so a stale worker cannot overwrite a newer attempt. Failed attempts back off
exponentially, with at most three attempts. Execution is at least once: a crash can
repeat a job, including any model charge, but only one result is ever committed.

**Actions.** A proposal freezes the run's message into an immutable payload and
publishes its SHA-256 digest. Only a reviewer from the same owner can approve, and
only by quoting that digest, so an approval cannot apply to content that changed.
Proposals expire after one hour. Delivery is one HTTPS POST with the action ID as
`Idempotency-Key`; a 2xx marks it `sent`, anything else (including timeouts) marks it
`unknown` rather than retrying an ambiguous side effect. Every transition is written
to an audit table under a row lock. Reconciling `unknown` actions is a manual step.

**Feedback and evaluation.** Reviews are immutable per run and reviewer (an identical
replay is accepted, a different one is a 409). Outcomes are deduplicated by owner and
event ID and can only link to an action from the same run. The report gives counts,
mean usefulness, pairwise safety agreement and the observed completion fraction, with
null where data is missing. The behavior suite (`python -m app.evaluation_suite`)
checks action choice, retrieval, citation validity, tool sequence and output format
on a versioned dataset, and `--baseline` rejects any per-case regression. See
[`docs/human-evaluation.md`](docs/human-evaluation.md) for the review rubric.

**Guardrails and limits.** Objective and context details allow 1 to 2,000 characters,
names and document IDs 1 to 100, and up to 20 inline documents of 1 to 10,000
characters, with 30,000 characters in total. Heuristics reject common instructions to
ignore prior rules, reveal secrets or bypass safeguards. They can miss attacks and
reject innocent text (for example "customer wants us to show them where their API
keys are"), so they are a first filter, not a complete prompt-injection defense.

**Operations.** Each response carries an `x-request-id`. Request logs are one JSON
line with the route template, status and duration, never paths, bodies or headers.
`/ready` fails with 503 when the database is unreachable, and the connection pool
validates connections on checkout, so after a PostgreSQL restart requests get fresh
connections instead of failing on dead ones (the e2e log shows no 5xx across one).
Workers stop claiming work on SIGTERM or SIGINT and exit after the current job.

## Verification

```sh
scripts/dev.sh bash scripts/verify.sh   # gate against real PostgreSQL 17 and Redis 7 in Docker
e2e/run.sh                              # full compose stack, writes e2e-out/report.json
```

`scripts/dev.sh` starts PostgreSQL and Redis containers on a private network and runs
the command in a Python container (`ADA_PYTHON=3.13` selects the version;
`scripts/dev.sh --down` removes the services). To run without Docker, install
`requirements-dev.txt` and set `TEST_DATABASE_URL` and `TEST_REDIS_URL`; without them
the real-service tests are skipped. The gate runs `pip check`, Ruff lint and format
checks, pytest with the 100% statement and branch coverage requirement, and the
behavior evaluation suite. The live model smoke test runs only with
`RUN_LIVE_PROVIDER_TEST=1` plus a real key and model, because it costs money.

The suite includes contract boundaries, Hypothesis properties for tokenization and
retrieval, guardrail cases with a documented false positive, 300 interleaved ASGI
requests with a negative control, real-database tests for migrations, pool
exhaustion and recovery, concurrent writes and ingestion, approval and delivery
races, forged and expired approvals, lease expiry, stale completion fencing, Redis
outages, retry exhaustion, and worker shutdown through a real SIGTERM.

`e2e/run.sh` builds the image and runs the compose stack with two test-only services
from [`e2e/stubs.py`](e2e/stubs.py): an HTTPS webhook sink behind a throwaway CA, and
a Responses API stub that rejects requests the real API would refuse (missing key,
non-strict schema, tools exposed). The driver in [`e2e/driver.py`](e2e/driver.py)
runs five phases against the API over HTTP:

1. **core:** authentication and owner isolation, knowledge ingestion and cited
   retrieval, guardrails, strict typing and body limits, idempotent jobs completed by
   the worker, approval with digest checks, HTTPS delivery received exactly once with
   the right idempotency key, the audit trail, rejection, reviews, outcomes, the
   evaluation report and metrics.
2. **queued:** with the worker stopped, a job stays queued and a cancelled job stays
   cancelled.
3. **drain:** after the worker starts again, the queued job completes and the
   cancelled one never runs.
4. **recovery:** after restarting PostgreSQL (the API is not restarted), readiness
   returns, stored runs and action states are intact, and new runs succeed.
5. **model:** after recreating the API and worker in `openai` mode, earlier data is
   still there, synchronous and queued runs use the model with valid citations and
   token metadata, and a provider outage returns 502 after exactly three attempts.

The worker is stopped twice with SIGTERM and must exit 0 well inside its grace
period. The stub proves the wiring and validation, not model quality; no paid model
call has been made for this repository.

The Stage 1 load reporter is still available for closed-loop local latency
observations (`python scripts/load.py --help`); its earlier results and limitations
are in [`docs/evidence/stage-1.md`](docs/evidence/stage-1.md).

## Limitations

- The default planner is a fixed rule, and evaluation metrics are structural
  diagnostics, not measures of message quality, factuality or business impact.
- Model mode has been exercised only against the stub and mocked responses.
- Authentication is static bearer tokens from configuration, with no rotation,
  expiry or external identity provider.
- Rate limits and metrics are per API process and in memory; behind a proxy every
  client shares one address unless the proxy forwards it to uvicorn.
- Knowledge search is a bounded exact scan of lexical hash vectors per owner, sized
  for small corpora.
- Jobs are at least once; actions are at most once, and `unknown` deliveries need
  manual reconciliation. There is no dead-letter queue or retry dashboard.
- Python 3.13 verifies TLS strictly, so a private CA for the webhook must carry key
  usage extensions (the e2e CA shows the required ones).
- There are no Kubernetes manifests, multi-region setup, tracing or long soak tests.

## Project history

The project was built in stages, each with its own evidence under
[`docs/evidence/`](docs/evidence): a verified deterministic baseline (Stage 1),
PostgreSQL storage and history (2), the bounded model provider (3), persistent
retrieval (4), authenticated approvals and audited actions (5), durable jobs and
workers (6), evaluation gates and feedback (7), and container operations (8).
Stages 2 to 7 were first opened as stacked pull requests; they were integrated with
the reviewed Stage 1 and the previously unpublished Stage 8 in one branch, where the
end-to-end run above was added. Decisions on the Stage 1 review are recorded in
[`docs/review-pr-1.md`](docs/review-pr-1.md).
