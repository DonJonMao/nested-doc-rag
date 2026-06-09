-- +goose Up
CREATE TABLE IF NOT EXISTS form_files (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    file_id UUID NOT NULL REFERENCES files(id) ON DELETE RESTRICT,
    filename TEXT NOT NULL,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_form_files_workspace_id ON form_files (workspace_id);
CREATE INDEX IF NOT EXISTS idx_form_files_file_id ON form_files (file_id);
CREATE INDEX IF NOT EXISTS idx_form_files_created_at ON form_files (created_at);

CREATE TABLE IF NOT EXISTS fill_runs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    form_file_id UUID NOT NULL REFERENCES form_files(id) ON DELETE RESTRICT,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    knowledge_base_id UUID,
    index_version_id UUID,
    target_namespace TEXT NOT NULL,
    global_namespace TEXT NOT NULL DEFAULT 'global',
    room_context TEXT,
    rows_spec TEXT NOT NULL,
    retrieval_mode TEXT NOT NULL DEFAULT 'layered',
    prompt_version TEXT NOT NULL DEFAULT 'step15_compat',
    judge_enabled BOOLEAN NOT NULL DEFAULT false,
    use_judge_cache BOOLEAN NOT NULL DEFAULT false,
    writeback_enabled BOOLEAN NOT NULL DEFAULT true,
    status TEXT NOT NULL DEFAULT 'created',
    progress_total INT NOT NULL DEFAULT 0,
    progress_done INT NOT NULL DEFAULT 0,
    out_dir TEXT,
    run_manifest_path TEXT,
    summary_path TEXT,
    filled_form_artifact_id UUID REFERENCES run_artifacts(id) ON DELETE SET NULL,
    answered_count INT NOT NULL DEFAULT 0,
    partial_clue_count INT NOT NULL DEFAULT 0,
    not_found_count INT NOT NULL DEFAULT 0,
    conflict_unresolved_count INT NOT NULL DEFAULT 0,
    review_required_count INT NOT NULL DEFAULT 0,
    writeback_allowed_count INT NOT NULL DEFAULT 0,
    failed_count INT NOT NULL DEFAULT 0,
    error_message TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    queued_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fill_runs_workspace_id ON fill_runs (workspace_id);
CREATE INDEX IF NOT EXISTS idx_fill_runs_form_file_id ON fill_runs (form_file_id);
CREATE INDEX IF NOT EXISTS idx_fill_runs_job_id ON fill_runs (job_id);
CREATE INDEX IF NOT EXISTS idx_fill_runs_status ON fill_runs (status);
CREATE INDEX IF NOT EXISTS idx_fill_runs_created_at ON fill_runs (created_at);
CREATE INDEX IF NOT EXISTS idx_fill_runs_target_namespace ON fill_runs (target_namespace);

-- +goose Down
DROP TABLE IF EXISTS fill_runs;
DROP TABLE IF EXISTS form_files;
