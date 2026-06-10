-- +goose Up
CREATE TABLE IF NOT EXISTS review_items (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES fill_runs(id) ON DELETE CASCADE,

    field_id TEXT,
    row_index INT,
    target_cell TEXT,
    question_text TEXT,

    answer_status TEXT,
    answer_value TEXT,
    confidence DOUBLE PRECISION,

    source_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_attachment_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    reference_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    reference_source_documents JSONB NOT NULL DEFAULT '[]'::jsonb,
    reference_snippets JSONB NOT NULL DEFAULT '[]'::jsonb,

    critic_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    risk_level TEXT NOT NULL DEFAULT 'medium',
    review_required BOOLEAN NOT NULL DEFAULT true,
    writeback_allowed BOOLEAN NOT NULL DEFAULT false,

    suggested_status TEXT,
    suggested_answer_value TEXT,
    suggested_reference_source_documents JSONB NOT NULL DEFAULT '[]'::jsonb,
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,

    status TEXT NOT NULL DEFAULT 'pending',
    reviewer_id UUID REFERENCES users(id),
    reviewed_at TIMESTAMPTZ,
    review_comment TEXT,
    edited_answer TEXT,

    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    overlay_payload JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_review_items_workspace_id ON review_items (workspace_id);
CREATE INDEX IF NOT EXISTS idx_review_items_run_id ON review_items (run_id);
CREATE INDEX IF NOT EXISTS idx_review_items_status ON review_items (status);
CREATE INDEX IF NOT EXISTS idx_review_items_risk_level ON review_items (risk_level);
CREATE INDEX IF NOT EXISTS idx_review_items_review_required ON review_items (review_required);
CREATE INDEX IF NOT EXISTS idx_review_items_writeback_allowed ON review_items (writeback_allowed);
CREATE INDEX IF NOT EXISTS idx_review_items_row_index ON review_items (row_index);
CREATE INDEX IF NOT EXISTS idx_review_items_target_cell ON review_items (target_cell);
CREATE INDEX IF NOT EXISTS idx_review_items_created_at ON review_items (created_at);

-- +goose Down
DROP TABLE IF EXISTS review_items;
