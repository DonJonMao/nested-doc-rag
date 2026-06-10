-- +goose Up
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    qdrant_collection TEXT,
    current_index_version_id UUID,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_knowledge_bases_workspace_name UNIQUE (workspace_id, name)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_bases_workspace_id ON knowledge_bases (workspace_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_name ON knowledge_bases (name);
CREATE INDEX IF NOT EXISTS idx_knowledge_bases_created_at ON knowledge_bases (created_at);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id UUID PRIMARY KEY,
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    file_id UUID NOT NULL REFERENCES files(id) ON DELETE RESTRICT,
    filename TEXT NOT NULL,
    document_role TEXT NOT NULL,
    namespace TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploaded',
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_workspace_id ON knowledge_documents (workspace_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_knowledge_base_id ON knowledge_documents (knowledge_base_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_file_id ON knowledge_documents (file_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_namespace ON knowledge_documents (namespace);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_document_role ON knowledge_documents (document_role);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_status ON knowledge_documents (status);
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_created_at ON knowledge_documents (created_at);

CREATE TABLE IF NOT EXISTS knowledge_index_versions (
    id UUID PRIMARY KEY,
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    version INT NOT NULL,
    qdrant_collection TEXT NOT NULL,
    qdrant_namespace TEXT,
    artifact_dir TEXT,
    manifest_path TEXT,
    status TEXT NOT NULL DEFAULT 'building',
    document_count INT NOT NULL DEFAULT 0,
    chunk_count INT NOT NULL DEFAULT 0,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ready_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    error_message TEXT,
    CONSTRAINT uq_knowledge_index_versions_base_version UNIQUE (knowledge_base_id, version)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_index_versions_workspace_id ON knowledge_index_versions (workspace_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_index_versions_knowledge_base_id ON knowledge_index_versions (knowledge_base_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_index_versions_status ON knowledge_index_versions (status);
CREATE INDEX IF NOT EXISTS idx_knowledge_index_versions_created_at ON knowledge_index_versions (created_at);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    knowledge_base_id UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    index_version_id UUID REFERENCES knowledge_index_versions(id) ON DELETE SET NULL,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'created',
    progress INT NOT NULL DEFAULT 0,
    document_count INT NOT NULL DEFAULT 0,
    error_message TEXT,
    python_command TEXT,
    out_dir TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_workspace_id ON ingestion_jobs (workspace_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_knowledge_base_id ON ingestion_jobs (knowledge_base_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_index_version_id ON ingestion_jobs (index_version_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_job_id ON ingestion_jobs (job_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs (status);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_created_at ON ingestion_jobs (created_at);

-- +goose Down
DROP TABLE IF EXISTS ingestion_jobs;
DROP TABLE IF EXISTS knowledge_index_versions;
DROP TABLE IF EXISTS knowledge_documents;
DROP TABLE IF EXISTS knowledge_bases;
