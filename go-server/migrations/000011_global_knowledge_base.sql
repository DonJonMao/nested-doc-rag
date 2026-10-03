-- +goose Up
INSERT INTO knowledge_bases (
    id, workspace_id, name, namespace, description, qdrant_collection,
    status, document_count, created_at, updated_at
)
SELECT gen_random_uuid(), w.id, '全局公共资料', 'global',
       '跨地点共用介绍、园区说明和通用材料',
       'datacenter_chunks_v1', 'empty', 0, now(), now()
FROM workspaces w
ON CONFLICT (workspace_id, namespace) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    qdrant_collection = EXCLUDED.qdrant_collection,
    updated_at = now();

-- +goose Down
DELETE FROM knowledge_bases
WHERE namespace = 'global'
  AND name = '全局公共资料';
