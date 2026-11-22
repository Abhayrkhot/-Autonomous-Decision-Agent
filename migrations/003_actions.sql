CREATE TABLE actions (
    action_id uuid PRIMARY KEY,
    run_id uuid UNIQUE NOT NULL REFERENCES runs(run_id),
    owner_id text NOT NULL,
    payload jsonb NOT NULL,
    digest text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','approved','rejected','dispatching','sent','unknown')),
    expires_at timestamptz NOT NULL
);
CREATE TABLE action_audit (
    event_id bigserial PRIMARY KEY,
    action_id uuid NOT NULL REFERENCES actions(action_id),
    actor text NOT NULL,
    event text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
