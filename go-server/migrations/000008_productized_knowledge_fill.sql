-- +goose Up
ALTER TABLE knowledge_bases
    ADD COLUMN IF NOT EXISTS namespace TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'empty',
    ADD COLUMN IF NOT EXISTS last_ingested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS document_count INT NOT NULL DEFAULT 0;

UPDATE knowledge_bases
SET namespace = 'kb_' || replace(id::text, '-', '')
WHERE namespace IS NULL OR btrim(namespace) = '';

ALTER TABLE knowledge_bases
    ALTER COLUMN namespace SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_bases_workspace_namespace
ON knowledge_bases (workspace_id, namespace);

CREATE INDEX IF NOT EXISTS idx_knowledge_bases_workspace_status
ON knowledge_bases (workspace_id, status, updated_at DESC);

ALTER TABLE knowledge_documents
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_ingested_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_kb_status_created_at
ON knowledge_documents (knowledge_base_id, status, created_at DESC);

ALTER TABLE fill_runs
    ADD COLUMN IF NOT EXISTS last_event_sequence BIGINT NOT NULL DEFAULT 0;

UPDATE fill_runs AS fr
SET last_event_sequence = event_sequences.last_sequence
FROM (
    SELECT run_id, COALESCE(MAX(sequence), 0) AS last_sequence
    FROM run_events
    GROUP BY run_id
) AS event_sequences
WHERE fr.id = event_sequences.run_id;

CREATE INDEX IF NOT EXISTS idx_fill_runs_workspace_created_by_status_created_at
ON fill_runs (workspace_id, created_by, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fill_runs_workspace_status_created_at
ON fill_runs (workspace_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fill_runs_kb_created_at
ON fill_runs (knowledge_base_id, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_run_events_run_sequence_unique
ON run_events (run_id, sequence);

INSERT INTO workspaces (id, name, description, created_at, updated_at)
VALUES (
    '00000000-0000-0000-0000-00000000dc01',
    '数据中心工勘平台',
    '默认工作区',
    now(),
    now()
)
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    updated_at = now();

INSERT INTO knowledge_bases (
    id, workspace_id, name, namespace, description, qdrant_collection,
    status, document_count, created_at, updated_at
)
SELECT gen_random_uuid(), w.id, v.name, v.namespace, v.description,
       'datacenter_chunks_v1', 'empty', 0, now(), now()
FROM workspaces w
CROSS JOIN (VALUES
    ('西咸1号楼', 'xixian_1', '西咸园区 1 号楼知识分库'),
    ('西咸2号楼', 'xixian_2', '西咸园区 2 号楼知识分库'),
    ('西咸3号楼', 'xixian_3', '西咸园区 3 号楼知识分库'),
    ('西咸4号楼', 'xixian_4', '西咸园区 4 号楼知识分库'),
    ('西咸5号楼', 'xixian_5', '西咸园区 5 号楼知识分库'),
    ('西咸6号楼', 'xixian_6', '西咸园区 6 号楼知识分库'),
    ('城东浐灞', 'chengdong_chanba', '城东浐灞知识分库'),
    ('西安', 'xian', '西安知识分库'),
    ('咸阳', 'xianyang', '咸阳知识分库')
) AS v(name, namespace, description)
WHERE w.id = '00000000-0000-0000-0000-00000000dc01'
ON CONFLICT (workspace_id, namespace) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    qdrant_collection = EXCLUDED.qdrant_collection,
    updated_at = now();

-- +goose Down
DROP INDEX IF EXISTS idx_run_events_run_sequence_unique;
DROP INDEX IF EXISTS idx_fill_runs_kb_created_at;
DROP INDEX IF EXISTS idx_fill_runs_workspace_status_created_at;
DROP INDEX IF EXISTS idx_fill_runs_workspace_created_by_status_created_at;
DROP INDEX IF EXISTS idx_knowledge_documents_kb_status_created_at;
DROP INDEX IF EXISTS idx_knowledge_bases_workspace_status;
DROP INDEX IF EXISTS idx_knowledge_bases_workspace_namespace;

ALTER TABLE fill_runs DROP COLUMN IF EXISTS last_event_sequence;
ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS last_ingested_at;
ALTER TABLE knowledge_documents DROP COLUMN IF EXISTS deleted_at;
ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS document_count;
ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS last_ingested_at;
ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS status;
ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS namespace;
