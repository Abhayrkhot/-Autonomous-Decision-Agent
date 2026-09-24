# Stage 5
Clean 7c55149: 98 passed and one explicit paid-provider skip. Coverage is recorded in
the attached log (99.26%). Real PostgreSQL tests cover owner separation, immutable
payload approval, expired/forged/replayed approvals, eight-way approval and delivery
races, audit ordering, and unknown delivery outcomes. HTTP effects were simulated
with MockTransport, never sent to real recipients. A crash in dispatching requires
manual reconciliation; external exactly-once delivery is not claimed. Full red-team
coverage and managed identity integration remain deployment-specific work.
