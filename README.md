# Autonomous Decision Agent

A small Python 3.11+ / FastAPI project that turns a business objective, customer
context, optional knowledge documents, and outcome signals into a recommended
action and a personalized message draft. It retrieves relevant context, runs two
local tools, evaluates the result, and stores the completed decision in memory.

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
  and `outcome_signals`. Unknown fields are rejected.
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
- Storage retains request, decision, tool results, and evaluation for the latest
  1,000 runs per process, evicting the oldest saved record. Reads and writes make
  defensive copies. Restarting loses all records; multiple workers do not share
  state. There is no public history endpoint, queue, retry policy, or persistence.
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

## Tests

```sh
python -m pytest -q
```

Tests cover a complete stored run, personalized source-bearing drafts, action
selection, deterministic results except UUIDs, retrieval ranking and ties,
no-match fallback, tool allowlisting, defensive storage copies and eviction,
health/API success, empty and long input, injection-style input in several fields,
aggregate limits, duplicate document IDs, and unknown request fields.

## Roadmap — not implemented

- **PostgreSQL:** durable run records behind `RunStore`, migrations and retention.
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

## Verification stages

Stage 1 adds unit, API, property-based, failure-injection, regression, and bounded
concurrency tests. Install `requirements-dev.txt` and run `bash scripts/verify.sh`.
The gate checks dependencies, lint, formatting, and a minimum 95% combined
statement/branch coverage. The Stage 1 run achieved 100% across application modules
with 70 passing cases; this is execution coverage, not proof of security or correctness.
CI repeats the suite on Python 3.11–3.13. Evidence and limitations are in `docs/evidence/`.

For a live load sample, start Uvicorn on port 8765 and run
`python scripts/load.py --output load.json`. Raw timings and environment metadata
are recorded; this is a local diagnostic, not a production capacity benchmark.

### Stage 2: durable storage

Export `DATABASE_URL`, run `python -m app.postgres` to apply checksummed transactional
migrations, then start Uvicorn. Without the variable, memory storage remains the default.
`GET /agent/runs?limit=20&offset=0` returns newest-first completed records;
`GET /agent/runs/{uuid}` retrieves one record. History is local and unauthenticated at
this stage: bind only to localhost. Failed attempts are not yet stored.

For real integration tests, set `TEST_DATABASE_URL` to a disposable PostgreSQL database
and run the verification script. Tests mutate the migration checksum temporarily, so
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
