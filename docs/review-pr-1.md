# PR #1 review decisions

These decisions apply to `stage-1-verification`. Later-stage work is separate.
Disagreements were explained to the requester before edits. This document records
the disposition of the review comments; it does not mark human review threads
resolved. See `docs/evidence/` for observed validation results.

## Contracts and retrieval

- [4089957854](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089957854)
  **Partly adopted.** Separate strict `RequestModel` and finite `ResponseModel`
  contracts, including nested requests. Responses remain mutable and storage keeps
  defensive copies. `frozen=True` is shallow; converting response lists to tuples
  still leaves the stored request and its nested documents mutable. Removing copies
  needs a separate complete immutable-record design. Both copy directions now have
  nested-mutation tests in `test_storage.py`.
- [4089959553](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089959553)
  **Adopted.** Shared `Identifier` constraint, with accepted/rejected boundaries.
- [4089972455](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089972455)
  **Adopted.** Retrieval rejects booleans and other non-integer limits, tested
  independently from zero and negative limits.
- [4089974838](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089974838)
  **Adopted; reproduced.** Round scores before filtering. The exact skewed input
  is pinned in a property test and an API regression: no zero-score result,
  citation, or inflated retrieved-document count.
- [4089978887](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089978887)
  **Adopted.** Documented compatibility change: string/integer booleans now return
  422. Nested valid JSON still returns 200; native Python coercions are rejected.

## CI, formatting and documentation

- [4089981237](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089981237)
  **Adopted.** Push triggers only on main, PR triggers remain, superseded runs cancel.
- [4089984143](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089984143)
  **Adopted.** Ubuntu 24.04 pinned. This reduces drift, not full environment variability.
- [4089987201](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089987201)
  **Adopted.** Python matrix has `fail-fast: false`.
- [4089989476](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089989476)
  **Adopted.** Checkout and setup-python upgraded to v7.
- [4089996904](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089996904),
  [4089997344](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089997344),
  [4089997889](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4089997889)
  **Disagree with removing import-group spacing.** The referenced blank lines
  conform to the configured formatter. Import groups retain that spacing; obsolete
  asyncio imports were removed when tests converted to AnyIO.
- [4090000979](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090000979)
  **Adopted.** One README tests/verification section.
- [4090002945](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090002945)
  **Adopted.** 100% statement/branch gate includes app and load reporter. The thin
  CLI entry wrapper is the sole explicit exclusion; `main()` is tested directly.

## Behavioral test improvements

- [4090009011](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090009011)
  **Adopted.** Shared `make_request` helper avoids fixture-name collisions.
- [4090010474](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090010474)
  **Adopted for current contract.** Full planner truth table checks actual action
  strings; constant plan-length assertion removed. An action enum is a future
  contract change, not claimed here.
- [4090035172](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090035172)
  **Adopted.** Separate score, limit, excerpt, default truncation and tie tests.
- [4090035452](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090035452)
  **Adopted.** Larger realistic-alphabet inputs, pinned repro, generated skewed
  near-limit counts, positive bounded scores, sorted results, arbitrary document
  permutations, result limits and shared-token invariants.
- [4090035700](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090035700)
  **Adopted.** Token properties check lowercase ASCII alphanumerics and stopword absence.
- [4090036371](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090036371)
  **Adopted.** Four accepted near-pattern texts plus the specified API-key-location
  false positive. Heuristics are unchanged; README names the limitation and explains
  that parameter combinations contribute to test counts.
- [4090044425](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090044425)
  **Adopted.** Async tests use AnyIO's pytest plugin with an asyncio backend.
- [4090044577](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090044577)
  **Adopted.** Separate read/write copy assertions, over-capacity eviction, and
  overwrite order. Overwriting makes a record newest for eviction.
- [4090070107](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090070107)
  **Adopted.** Same document ID with different text must change the cited content.
- [4090070189](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090070189)
  **Adopted for current contract.** Keyword `ToolInput` arguments. Follow-up output
  remains a string; its assertion documents current behavior until typed outputs exist.
- [4090070357](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090070357)
  **Already satisfied, verified.** Stress marker registered and full gate includes it.
- [4090070508](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090070508)
  **Adopted.** Yielding tool forces actual interleaving. A test-only shared-state
  agent with an await demonstrably fails the same request-isolation assertion.
- [4090070848](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090070848)
  **Adopted.** Standalone UUID uniqueness check removed.
- [4090070991](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090070991)
  **Adopted.** Exactly 50 of 300 returned IDs remain accessible through `store.get`;
  no private-record inspection.
- [4090098365](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090098365)
  **Adopted.** Maximum-field inputs use spaced words that actually retrieve evidence.
- [4090098440](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090098440)
  **Investigation disagrees with the 4,200-character estimate.** The template limits
  context and excerpt separately to 300 characters. With current planner actions,
  maximum-field drafts are at most 2,970 characters; there is no final-message
  truncation. Both action paths preserve citation, excerpt and closing question.
  The evaluator's false branch is tested directly with a 4,001-character message;
  that branch is diagnostic for future generators, unreachable from today's template.
- [4090098667](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090098667)
  **Adopted.** Tests accept 20 documents and reject 21, and check text-length boundaries.

## Load observations

- [4090137162](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090137162)
  **Adopted.** Pure summary function with hand-calculated expected values; load
  orchestration and CLI also tested and included in the coverage gate.
- [4090137335](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090137335)
  **Adopted.** Both connection limits equal concurrency; configurable timeout.
- [4090137362](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090137362)
  **Adopted.** Explicit closed-loop/client-wait exclusion and distinct batch/request
  clocks. Open-loop scheduled-send measurements remain future work.
- [4090137565](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090137565)
  **Adopted.** Per-request HTTP errors retained; health preflight failures saved.
- [4090153804](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090153804)
  **Adopted.** Consistent inclusive P50/P95/P99, max, attempted/successful throughput,
  and explicit zero/single-success handling.
- [4090154040](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090154040)
  **Adopted.** Configurable warmup before each separately reported repeat. Warmup
  summaries are retained but excluded from measured percentiles.
- [4090154482](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090154482)
  **Adopted.** CPU count, UTC start, and operator-supplied server notes.
- [4090154730](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090154730)
  **Adopted with explicit boundary.** Client revision and dirty status captured
  before load, including untracked changes; missing Git is tolerated. Server
  revision is separately operator-supplied. Health-based version attestation is deferred.
- [4090156897](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090156897)
  **Adopted.** Three-document default payload and custom JSON payload option.
- [4090157084](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090157084)
  **Adopted.** Raw index/start offset/latency/status; latency percentiles only use 200s.
- [4090157267](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090157267)
  **Adopted.** `Counter` aggregates statuses.
- [4090157693](https://github.com/Abhayrkhot/-Autonomous-Decision-Agent/pull/1#discussion_r4090157693)
  **Adopted.** Updated reporter and regenerated evidence replace the old observations;
  run results describe this local setup, not production performance.
