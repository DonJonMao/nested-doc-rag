-- +goose Up
CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id UUID NOT NULL,
    status TEXT NOT NULL,
    priority INT NOT NULL DEFAULT 0,
    attempt INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT,
    cancel_requested_at TIMESTAMPTZ,
    queued_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_workspace_id ON jobs (workspace_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_job_type ON jobs (job_type);
CREATE INDEX IF NOT EXISTS idx_jobs_resource ON jobs (resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs (created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_queued_at ON jobs (queued_at);
CREATE INDEX IF NOT EXISTS idx_jobs_heartbeat_at ON jobs (heartbeat_at);

CREATE TABLE IF NOT EXISTS run_events (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id UUID NOT NULL,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    sequence BIGSERIAL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_run_events_workspace_id ON run_events (workspace_id);
CREATE INDEX IF NOT EXISTS idx_run_events_run_sequence ON run_events (run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_run_events_job_id ON run_events (job_id);
CREATE INDEX IF NOT EXISTS idx_run_events_event_type ON run_events (event_type);
CREATE INDEX IF NOT EXISTS idx_run_events_created_at ON run_events (created_at);

-- +goose Down
DROP TABLE IF EXISTS run_events;
DROP TABLE IF EXISTS jobs;
