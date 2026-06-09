-- +goose Up
CREATE TABLE IF NOT EXISTS files (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    file_size BIGINT NOT NULL,
    mime_type TEXT,
    sha256 TEXT NOT NULL,
    file_category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_files_workspace_id ON files (workspace_id);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files (sha256);
CREATE INDEX IF NOT EXISTS idx_files_file_category ON files (file_category);
CREATE INDEX IF NOT EXISTS idx_files_status ON files (status);
CREATE INDEX IF NOT EXISTS idx_files_created_at ON files (created_at);

CREATE TABLE IF NOT EXISTS run_artifacts (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id UUID NOT NULL,
    artifact_type TEXT NOT NULL,
    filename TEXT NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    local_path TEXT,
    content_type TEXT,
    file_size BIGINT,
    sha256 TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_run_artifacts_workspace_id ON run_artifacts (workspace_id);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_run_id ON run_artifacts (run_id);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_artifact_type ON run_artifacts (artifact_type);
CREATE INDEX IF NOT EXISTS idx_run_artifacts_created_at ON run_artifacts (created_at);

-- +goose Down
DROP TABLE IF EXISTS run_artifacts;
DROP TABLE IF EXISTS files;
