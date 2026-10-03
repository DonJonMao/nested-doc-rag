-- +goose Up
UPDATE knowledge_bases AS kb
SET namespace = 'chengdong_baqiao',
    name = '城东灞桥',
    description = '城东灞桥知识分库',
    updated_at = now()
WHERE kb.namespace = 'chengdong_chanba'
  AND NOT EXISTS (
      SELECT 1
      FROM knowledge_bases AS existing
      WHERE existing.workspace_id = kb.workspace_id
        AND existing.namespace = 'chengdong_baqiao'
  );

UPDATE knowledge_bases
SET status = 'archived',
    updated_at = now()
WHERE namespace = 'chengdong_chanba';

UPDATE knowledge_documents
SET namespace = 'chengdong_baqiao',
    updated_at = now()
WHERE namespace = 'chengdong_chanba';

UPDATE fill_runs
SET target_namespace = 'chengdong_baqiao',
    updated_at = now()
WHERE target_namespace = 'chengdong_chanba';

CREATE TEMP TABLE tmp_gongkan_preprocessed_kb_seed (
    namespace TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    chunk_count INT NOT NULL,
    document_count INT NOT NULL
) ON COMMIT DROP;

INSERT INTO tmp_gongkan_preprocessed_kb_seed (namespace, name, description, chunk_count, document_count)
VALUES
    ('xianyang', '咸阳', '咸阳知识分库', 1550, 2),
    ('xian', '西安', '西安知识分库', 1508, 2),
    ('chengdong_baqiao', '城东灞桥', '城东灞桥知识分库', 367, 2),
    ('xixian_1', '西咸1号楼', '西咸园区 1 号楼知识分库', 115, 1),
    ('xixian_2', '西咸2号楼', '西咸园区 2 号楼知识分库', 1568, 1),
    ('xixian_3', '西咸3号楼', '西咸园区 3 号楼知识分库', 115, 1),
    ('xixian_4', '西咸4号楼', '西咸园区 4 号楼知识分库', 1461, 1),
    ('xixian_5', '西咸5号楼', '西咸园区 5 号楼知识分库', 189, 1),
    ('xixian_6', '西咸6号楼', '西咸园区 6 号楼知识分库', 1437, 1),
    ('global', '全局公共资料', '跨地点共用介绍、园区说明和通用材料', 1223, 2);

CREATE TEMP TABLE tmp_gongkan_preprocessed_doc_seed (
    namespace TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_id UUID PRIMARY KEY,
    document_id UUID NOT NULL,
    object_key TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    mime_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    document_role TEXT NOT NULL
) ON COMMIT DROP;

INSERT INTO tmp_gongkan_preprocessed_doc_seed (
    namespace, original_filename, filename, file_id, document_id, object_key,
    file_size, mime_type, sha256, document_role
)
VALUES
    ('xianyang', '中国移动（陕西咸阳）数据中心维护能力知识库 .xlsx', '中国移动陕西咸阳数据中心维护能力知识库 .xlsx', '638cb027-36f1-524d-afde-fc8752fd6608', '8d4d1ed4-227b-5c8e-84fc-8e96eebbcd91', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/638cb027-36f1-524d-afde-fc8752fd6608/中国移动陕西咸阳数据中心维护能力知识库 .xlsx', 280581047, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'e7a047c08f2e864e465ff83dc82cbbf812cb109e7d9c0a518c2bd851916e9a65', 'knowledge_base'),
    ('xianyang', '中国移动（陕西咸阳）数据中心机房情况说明介绍.docx', '中国移动陕西咸阳数据中心机房情况说明介绍.docx', 'b916fbd0-6c0e-5229-b2b0-9d07bb0ac8e8', '601d6c81-5101-51dc-8638-b62650da0465', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/b916fbd0-6c0e-5229-b2b0-9d07bb0ac8e8/中国移动陕西咸阳数据中心机房情况说明介绍.docx', 415931, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'b416dba6c5aa40b3114057e35743b815c3a5b73b0a412ae6b7f86c348e4f36a7', 'intro_doc'),
    ('xian', '中国移动（陕西西安）数据中心机房维护能力知识库.xlsx', '中国移动陕西西安数据中心机房维护能力知识库.xlsx', '7ed8a8cd-6f9e-50cd-a7d9-49745688f6b1', 'f48807cc-0105-5c27-b05e-283f3f1642ae', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/7ed8a8cd-6f9e-50cd-a7d9-49745688f6b1/中国移动陕西西安数据中心机房维护能力知识库.xlsx', 28617130, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'f581fc998f60fec2981439252cdc118273ae3a88d094d6f568a8f53f951fee77', 'knowledge_base'),
    ('xian', '中国移动（陕西西安）数据中心机房情况说明介绍.docx', '中国移动陕西西安数据中心机房情况说明介绍.docx', '256c8f6e-5c01-5de9-81a2-77d81eb93797', 'd30cb2aa-c311-538e-a2b8-67c171830f05', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/256c8f6e-5c01-5de9-81a2-77d81eb93797/中国移动陕西西安数据中心机房情况说明介绍.docx', 249945, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '143d901ecc989d108df974d56a5b587a7186c4ab33ed0ca5cac7e8835b0e64a0', 'intro_doc'),
    ('chengdong_baqiao', '城东数据中心维护能力知识库.xlsx', '城东数据中心维护能力知识库.xlsx', 'ca70634a-3ed3-5e9c-8fd9-08ad1c07ac2b', 'd64c1798-31f1-5fcf-8d7a-6e76ec9408d9', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/ca70634a-3ed3-5e9c-8fd9-08ad1c07ac2b/城东数据中心维护能力知识库.xlsx', 123745729, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '2dc997806ef04da2cd15e7f4219262eff72eb07621353ad37c13ed9e095ae840', 'knowledge_base'),
    ('chengdong_baqiao', '中国移动（西安灞桥）数据中心机房情况说明介绍.docx', '中国移动西安灞桥数据中心机房情况说明介绍.docx', '2e43d37c-5e32-5879-b990-db2f278a7633', '9c05f806-51bc-5693-a5bf-59cb0c79b2b7', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/2e43d37c-5e32-5879-b990-db2f278a7633/中国移动西安灞桥数据中心机房情况说明介绍.docx', 30937, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '0cf49a1f6a49e1e990ff4c7c215f5a03731dd78c18c35f5b33ae19e64a926f64', 'intro_doc'),
    ('xixian_1', '西咸数据中心1号楼维护能力知识库.xlsx', '西咸数据中心1号楼维护能力知识库.xlsx', '2c0e1725-50dd-549d-9e60-0a20e10f70cf', 'ed2f87de-eb49-593a-a8b0-6af67902ed5a', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/2c0e1725-50dd-549d-9e60-0a20e10f70cf/西咸数据中心1号楼维护能力知识库.xlsx', 24193137, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '3679b233ee452a0a637cce574012053caa9c8578ccdba1b7f03c5bcca7e3edb6', 'knowledge_base'),
    ('xixian_2', '西咸数据中心2号楼维护能力知识库.xlsx', '西咸数据中心2号楼维护能力知识库.xlsx', 'd1c62abd-63d4-542f-b517-033b4bb683a4', '3bc35206-b57d-5854-9307-9854e1782645', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/d1c62abd-63d4-542f-b517-033b4bb683a4/西咸数据中心2号楼维护能力知识库.xlsx', 804124557, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '4a3e581ea7a8178b0cc826945364788c4e9338a0b2551dcb0f2da42898b10bbc', 'knowledge_base'),
    ('xixian_3', '西咸数据中心3号楼维护能力知识库.xlsx', '西咸数据中心3号楼维护能力知识库.xlsx', '13515794-1668-511d-b443-b821f59b2e74', '5f847ae0-8b7c-5932-8936-fe0f64fc2bb4', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/13515794-1668-511d-b443-b821f59b2e74/西咸数据中心3号楼维护能力知识库.xlsx', 67197362, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'f0b1dafaec796ce26e66aea73e8604c000746b7634bccaa15120e84ee5fe3855', 'knowledge_base'),
    ('xixian_4', '西咸数据中心4号楼维护能力知识库.xlsx', '西咸数据中心4号楼维护能力知识库.xlsx', 'b709e6b0-4a19-5773-b363-96b648272547', '51246b3f-d859-51b3-bf8c-18053bee1a26', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/b709e6b0-4a19-5773-b363-96b648272547/西咸数据中心4号楼维护能力知识库.xlsx', 187689946, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '3e5730553f4cefc06bb99d2c3f32187309804c73ada00900dfe530809eb6b247', 'knowledge_base'),
    ('xixian_5', '西咸数据中心5号楼维护能力知识库.xlsx', '西咸数据中心5号楼维护能力知识库.xlsx', '89372d61-3950-5fc3-8456-884d30fe52ac', '92d6abec-d828-5cac-af14-3eb02e16bf89', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/89372d61-3950-5fc3-8456-884d30fe52ac/西咸数据中心5号楼维护能力知识库.xlsx', 626617386, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'bfb66c7e324e04c0df7875e1f99f17d68735a500e561acf696424221f7eb7a05', 'knowledge_base'),
    ('xixian_6', '西咸数据中心6号楼维护能力知识库.xlsx', '西咸数据中心6号楼维护能力知识库.xlsx', '4df28f30-872d-5541-a8b8-08a35b49372a', 'e71b83c6-65d5-5d12-a0c6-d9aceb4486ee', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/4df28f30-872d-5541-a8b8-08a35b49372a/西咸数据中心6号楼维护能力知识库.xlsx', 588449141, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'c84fd8924626126a2ce7e190696e2581b88ff90a713eaea742bdd59029179932', 'knowledge_base'),
    ('global', '陕西移动IDC对外服务知识库.xlsx', '陕西移动IDC对外服务知识库.xlsx', '05ff2255-2dd8-593a-bd17-9a0de96a8594', 'ec9218ae-c796-52b2-abc6-712e8ae4fb31', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/05ff2255-2dd8-593a-bd17-9a0de96a8594/陕西移动IDC对外服务知识库.xlsx', 4445430, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'b9ee4785faca338c61dfc00ae86142f953f42ba7e28894cd8f66913c288b50dd', 'knowledge_base'),
    ('global', '中国移动（陕西西咸）数据中心机房情况说明介绍.docx', '中国移动陕西西咸数据中心机房情况说明介绍.docx', '200e3b67-1482-555e-9653-f57ff841ce92', '17553527-72ac-5679-bba4-73d3df458398', 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/200e3b67-1482-555e-9653-f57ff841ce92/中国移动陕西西咸数据中心机房情况说明介绍.docx', 548307, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '1f8fd6d3927193799619a1abe88e541bc080f24ddda2cccee67e88b8cfc716e1', 'intro_doc');

INSERT INTO knowledge_bases (
    id, workspace_id, name, namespace, description, qdrant_collection,
    status, document_count, last_ingested_at, created_at, updated_at
)
SELECT uuid_generate_v5(uuid_ns_url(), 'gongkan:knowledge_base:' || seed.namespace),
       w.id,
       seed.name,
       seed.namespace,
       seed.description,
       'datacenter_chunks_v1',
       'ready',
       seed.document_count,
       now(),
       now(),
       now()
FROM tmp_gongkan_preprocessed_kb_seed AS seed
CROSS JOIN workspaces AS w
WHERE w.id = '00000000-0000-0000-0000-00000000dc01'
ON CONFLICT (workspace_id, namespace) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    qdrant_collection = EXCLUDED.qdrant_collection,
    status = 'ready',
    document_count = EXCLUDED.document_count,
    last_ingested_at = now(),
    updated_at = now();

INSERT INTO files (
    id, workspace_id, filename, original_filename, object_key, file_size,
    mime_type, sha256, file_category, status, created_at
)
SELECT doc.file_id,
       w.id,
       doc.filename,
       doc.original_filename,
       doc.object_key,
       doc.file_size,
       doc.mime_type,
       doc.sha256,
       'knowledge_document',
       'active',
       now()
FROM tmp_gongkan_preprocessed_doc_seed AS doc
CROSS JOIN workspaces AS w
WHERE w.id = '00000000-0000-0000-0000-00000000dc01'
ON CONFLICT (id) DO UPDATE
SET filename = EXCLUDED.filename,
    original_filename = EXCLUDED.original_filename,
    object_key = EXCLUDED.object_key,
    file_size = EXCLUDED.file_size,
    mime_type = EXCLUDED.mime_type,
    sha256 = EXCLUDED.sha256,
    file_category = EXCLUDED.file_category,
    status = 'active',
    deleted_at = NULL;

INSERT INTO knowledge_documents (
    id, knowledge_base_id, workspace_id, file_id, filename, document_role,
    namespace, status, created_at, updated_at, last_ingested_at
)
SELECT doc.document_id,
       kb.id,
       kb.workspace_id,
       doc.file_id,
       doc.filename,
       doc.document_role,
       doc.namespace,
       'indexed',
       now(),
       now(),
       now()
FROM tmp_gongkan_preprocessed_doc_seed AS doc
JOIN knowledge_bases AS kb
  ON kb.namespace = doc.namespace
 AND kb.workspace_id = '00000000-0000-0000-0000-00000000dc01'
ON CONFLICT (id) DO UPDATE
SET knowledge_base_id = EXCLUDED.knowledge_base_id,
    workspace_id = EXCLUDED.workspace_id,
    file_id = EXCLUDED.file_id,
    filename = EXCLUDED.filename,
    document_role = EXCLUDED.document_role,
    namespace = EXCLUDED.namespace,
    status = 'indexed',
    deleted_at = NULL,
    last_ingested_at = now(),
    updated_at = now();

INSERT INTO knowledge_index_versions (
    id, knowledge_base_id, workspace_id, version, qdrant_collection,
    qdrant_namespace, artifact_dir, manifest_path, status,
    document_count, chunk_count, created_at, ready_at
)
SELECT uuid_generate_v5(uuid_ns_url(), 'gongkan:knowledge_index_version:' || seed.namespace || ':preprocessed-v1'),
       kb.id,
       kb.workspace_id,
       1,
       'datacenter_chunks_v1',
       seed.namespace,
       'preprocessed://artifacts/15_vector_store/qdrant',
       'preprocessed://artifacts/15_vector_store/expanded_ingestion_manifest.jsonl',
       'ready',
       seed.document_count,
       seed.chunk_count,
       now(),
       now()
FROM tmp_gongkan_preprocessed_kb_seed AS seed
JOIN knowledge_bases AS kb
  ON kb.namespace = seed.namespace
 AND kb.workspace_id = '00000000-0000-0000-0000-00000000dc01'
ON CONFLICT (knowledge_base_id, version) DO UPDATE
SET qdrant_collection = EXCLUDED.qdrant_collection,
    qdrant_namespace = EXCLUDED.qdrant_namespace,
    artifact_dir = EXCLUDED.artifact_dir,
    manifest_path = EXCLUDED.manifest_path,
    status = 'ready',
    document_count = EXCLUDED.document_count,
    chunk_count = EXCLUDED.chunk_count,
    ready_at = now(),
    failed_at = NULL,
    error_message = NULL;

UPDATE knowledge_bases AS kb
SET current_index_version_id = version.id,
    status = 'ready',
    document_count = seed.document_count,
    last_ingested_at = now(),
    updated_at = now()
FROM tmp_gongkan_preprocessed_kb_seed AS seed
JOIN knowledge_index_versions AS version
  ON version.qdrant_namespace = seed.namespace
 AND version.version = 1
WHERE kb.namespace = seed.namespace
  AND kb.workspace_id = '00000000-0000-0000-0000-00000000dc01'
  AND version.knowledge_base_id = kb.id;

-- +goose Down
DELETE FROM knowledge_documents
WHERE id IN (
    '8d4d1ed4-227b-5c8e-84fc-8e96eebbcd91',
    '601d6c81-5101-51dc-8638-b62650da0465',
    'f48807cc-0105-5c27-b05e-283f3f1642ae',
    'd30cb2aa-c311-538e-a2b8-67c171830f05',
    'd64c1798-31f1-5fcf-8d7a-6e76ec9408d9',
    '9c05f806-51bc-5693-a5bf-59cb0c79b2b7',
    'ed2f87de-eb49-593a-a8b0-6af67902ed5a',
    '3bc35206-b57d-5854-9307-9854e1782645',
    '5f847ae0-8b7c-5932-8936-fe0f64fc2bb4',
    '51246b3f-d859-51b3-bf8c-18053bee1a26',
    '92d6abec-d828-5cac-af14-3eb02e16bf89',
    'e71b83c6-65d5-5d12-a0c6-d9aceb4486ee',
    'ec9218ae-c796-52b2-abc6-712e8ae4fb31',
    '17553527-72ac-5679-bba4-73d3df458398'
);

DELETE FROM files
WHERE id IN (
    '638cb027-36f1-524d-afde-fc8752fd6608',
    'b916fbd0-6c0e-5229-b2b0-9d07bb0ac8e8',
    '7ed8a8cd-6f9e-50cd-a7d9-49745688f6b1',
    '256c8f6e-5c01-5de9-81a2-77d81eb93797',
    'ca70634a-3ed3-5e9c-8fd9-08ad1c07ac2b',
    '2e43d37c-5e32-5879-b990-db2f278a7633',
    '2c0e1725-50dd-549d-9e60-0a20e10f70cf',
    'd1c62abd-63d4-542f-b517-033b4bb683a4',
    '13515794-1668-511d-b443-b821f59b2e74',
    'b709e6b0-4a19-5773-b363-96b648272547',
    '89372d61-3950-5fc3-8456-884d30fe52ac',
    '4df28f30-872d-5541-a8b8-08a35b49372a',
    '05ff2255-2dd8-593a-bd17-9a0de96a8594',
    '200e3b67-1482-555e-9653-f57ff841ce92'
);
