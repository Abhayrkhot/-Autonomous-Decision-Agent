# Human evaluation rubric v1

Review the stored request, retrieved evidence, draft, and tool results together.
Record usefulness from 1 (unusable) to 5 (usable without substantive correction).
Record correctness, grounding, and safety as independent booleans, with reasons in
notes. Do not infer factuality from a citation existing or objective-token overlap.
Use separate reviewer identities and discuss disagreements; pairwise safety agreement
is descriptive only and is not chance-corrected. A single review has no agreement score.

Reviews are immutable per run/reviewer. Correct disputed labels through a new review
identity/version with an audit explanation; never silently rewrite existing judgments.
Outcome events are deduplicated by owner/event UUID and optionally linked to an action
from the same run. Observations do not establish causality or conversion improvement.
No automatic prompt or policy changes are made from feedback.

The versioned behavior dataset is synthetic and intentionally small. Keep development
cases separate from a future held-out, human-labelled dataset. No human labels or
model-quality improvements are claimed by the automated fixture tests.
