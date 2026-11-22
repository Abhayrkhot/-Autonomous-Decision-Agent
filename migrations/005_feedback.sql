CREATE TABLE reviews (
    run_id uuid NOT NULL REFERENCES runs(run_id),
    reviewer text NOT NULL,
    body jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id,reviewer)
);
CREATE TABLE feedback (
    owner_id text NOT NULL,
    event_id uuid NOT NULL,
    run_id uuid NOT NULL REFERENCES runs(run_id),
    body jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id,event_id)
);
