# Stage 6
Clean revision 62ffa8e passed 103 tests, with one explicit paid-provider skip. The
exact combined coverage is in stage-6-verification.txt. Real PostgreSQL and Redis
verified 50 concurrent jobs, idempotency conflicts, bounded pending work, stale
leases, retry exhaustion, cancellation fencing, temporary worker database errors,
and successful completion with unavailable Redis. Redis carries notifications only.
This is a bounded reliability suite, not an hours-long soak or process-kill test.
