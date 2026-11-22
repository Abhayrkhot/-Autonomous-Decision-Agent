CREATE TABLE jobs (
    job_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    idempotency_key text NOT NULL,
    request jsonb NOT NULL,
    digest text NOT NULL,
    status text NOT NULL CHECK (status IN ('queued','running','completed','failed','cancelled')),
    attempts integer NOT NULL DEFAULT 0,
    lease_token uuid,
    lease_until timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    result_id uuid,
    error_code text,
    UNIQUE(owner_id, idempotency_key)
);
CREATE INDEX jobs_claim ON jobs(status, next_attempt_at, created_at);
