CREATE TABLE runs (
    run_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    created_at timestamptz NOT NULL,
    record jsonb NOT NULL
);
CREATE INDEX runs_owner_created ON runs (owner_id, created_at DESC, run_id DESC);
