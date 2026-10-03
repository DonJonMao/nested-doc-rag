-- Only start.sh's pre-volume fresh-install marker authorizes this transaction.
-- Never run this file as an upgrade or against a database containing user work.
BEGIN;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '30s';
SELECT pg_advisory_xact_lock(1852076658, 1);
LOCK TABLE workspaces, files, form_files, jobs, fill_runs, ingestion_jobs,
  run_artifacts, review_items, knowledge_bases, knowledge_documents,
  knowledge_index_versions, knowledge_version_source_pins,
  fill_run_index_pins, fill_run_template_pins IN SHARE ROW EXCLUSIVE MODE;

CREATE TEMP TABLE offline_seed_namespaces(namespace text PRIMARY KEY) ON COMMIT DROP;
INSERT INTO offline_seed_namespaces VALUES
  ('xianyang'), ('xian'), ('chengdong_baqiao'), ('xixian_1'), ('xixian_2'),
  ('xixian_3'), ('xixian_4'), ('xixian_5'), ('xixian_6'), ('global');
CREATE TEMP TABLE offline_seed_files(id uuid PRIMARY KEY) ON COMMIT DROP;
INSERT INTO offline_seed_files VALUES
  ('638cb027-36f1-524d-afde-fc8752fd6608'), ('b916fbd0-6c0e-5229-b2b0-9d07bb0ac8e8'),
  ('7ed8a8cd-6f9e-50cd-a7d9-49745688f6b1'), ('256c8f6e-5c01-5de9-81a2-77d81eb93797'),
  ('ca70634a-3ed3-5e9c-8fd9-08ad1c07ac2b'), ('9c05f806-51bc-5693-a5bf-59cb0c79b2b7'),
  ('2c0e1725-50dd-549d-9e60-0a20e10f70cf'), ('3bc35206-b57d-5854-9307-9854e1782645'),
  ('13515794-1668-511d-b443-b821f59b2e74'), ('b709e6b0-4a19-5773-b363-96b648272547'),
  ('89372d61-3950-5fc3-8456-884d30fe52ac'), ('4df28f30-872d-5541-a8b8-08a35b49372a'),
  ('05ff2255-2dd8-593a-bd17-9a0de96a8594'), ('200e3b67-1482-555e-9653-f57ff841ce92');

DO $$
BEGIN
  IF (SELECT count(*) FROM gongkan_schema_migrations) <> 13 OR EXISTS (
    SELECT 1 FROM gongkan_schema_migrations WHERE version NOT BETWEEN 1 AND 13
      OR origin <> 'executed' OR adoption_id IS NOT NULL
  ) THEN RAISE EXCEPTION 'fresh cleanup requires exactly the fresh executed 1-13 migration ledger'; END IF;
  IF (SELECT count(*) FROM workspaces) <> 1 OR NOT EXISTS (
    SELECT 1 FROM workspaces WHERE id = '00000000-0000-0000-0000-00000000dc01'
  ) THEN RAISE EXCEPTION 'fresh cleanup refused: unexpected workspace'; END IF;
  IF EXISTS (SELECT 1 FROM jobs) OR EXISTS (SELECT 1 FROM fill_runs)
    OR EXISTS (SELECT 1 FROM ingestion_jobs) OR EXISTS (SELECT 1 FROM form_files)
    OR EXISTS (SELECT 1 FROM run_artifacts) OR EXISTS (SELECT 1 FROM review_items)
    OR EXISTS (SELECT 1 FROM knowledge_version_source_pins)
    OR EXISTS (SELECT 1 FROM fill_run_index_pins)
    OR EXISTS (SELECT 1 FROM fill_run_template_pins)
  THEN RAISE EXCEPTION 'fresh cleanup refused: user job, form, artifact, review or pin exists'; END IF;

  -- A committed cleanup with an interrupted marker update is safe to retry.
  IF NOT EXISTS (SELECT 1 FROM knowledge_bases) AND NOT EXISTS (SELECT 1 FROM files)
    AND NOT EXISTS (SELECT 1 FROM knowledge_documents) AND NOT EXISTS (SELECT 1 FROM knowledge_index_versions)
  THEN RETURN; END IF;

  IF (SELECT count(*) FROM knowledge_bases) <> 10 OR EXISTS (
    SELECT 1 FROM knowledge_bases kb WHERE workspace_id <> '00000000-0000-0000-0000-00000000dc01'
      OR namespace NOT IN (SELECT namespace FROM offline_seed_namespaces)
      OR status <> 'ready' OR source_revision <> 0 OR source_dirty OR activation_revision <> 0
      OR current_index_version_id IS DISTINCT FROM uuid_generate_v5(uuid_ns_url(), 'gongkan:knowledge_index_version:' || namespace || ':preprocessed-v1')
  ) THEN RAISE EXCEPTION 'fresh cleanup refused: knowledge bases differ from frozen bootstrap'; END IF;
  IF (SELECT count(*) FROM files) <> 14 OR EXISTS (
    SELECT 1 FROM files WHERE id NOT IN (SELECT id FROM offline_seed_files)
      OR workspace_id <> '00000000-0000-0000-0000-00000000dc01'
      OR file_category <> 'knowledge_document' OR status <> 'active' OR deleted_at IS NOT NULL
      OR object_key NOT LIKE 'workspaces/00000000-0000-0000-0000-00000000dc01/documents/%'
  ) THEN RAISE EXCEPTION 'fresh cleanup refused: files differ from frozen bootstrap'; END IF;
  IF (SELECT count(*) FROM knowledge_documents) <> 14 OR EXISTS (
    SELECT 1 FROM knowledge_documents WHERE file_id NOT IN (SELECT id FROM offline_seed_files)
      OR workspace_id <> '00000000-0000-0000-0000-00000000dc01'
      OR namespace NOT IN (SELECT namespace FROM offline_seed_namespaces)
      OR status <> 'indexed' OR deleted_at IS NOT NULL
  ) THEN RAISE EXCEPTION 'fresh cleanup refused: documents differ from frozen bootstrap'; END IF;
  IF (SELECT count(*) FROM knowledge_index_versions) <> 10 OR EXISTS (
    SELECT 1 FROM knowledge_index_versions v WHERE workspace_id <> '00000000-0000-0000-0000-00000000dc01'
      OR qdrant_namespace NOT IN (SELECT namespace FROM offline_seed_namespaces)
      OR id <> uuid_generate_v5(uuid_ns_url(), 'gongkan:knowledge_index_version:' || qdrant_namespace || ':preprocessed-v1')
      OR version <> 1 OR status <> 'ready' OR storage_contract <> 'legacy_unversioned'
      OR validation_state <> 'legacy_declared_ready' OR publication_state <> 'unpublished'
      OR input_snapshot_json IS NOT NULL OR validation_receipt_json IS NOT NULL
      OR artifact_dir <> 'preprocessed://artifacts/15_vector_store/qdrant'
      OR manifest_path <> 'preprocessed://artifacts/15_vector_store/expanded_ingestion_manifest.jsonl'
      OR knowledge_base_id IS DISTINCT FROM (SELECT kb.id FROM knowledge_bases kb
        WHERE kb.workspace_id = v.workspace_id AND kb.namespace = v.qdrant_namespace)
  ) THEN RAISE EXCEPTION 'fresh cleanup refused: index versions differ from frozen bootstrap'; END IF;
END $$;

-- Break the current-version FK before deleting legacy versions. User pins/runs
-- were asserted empty above; documents are removed before their file FK targets.
UPDATE knowledge_bases SET current_index_version_id = NULL
WHERE workspace_id = '00000000-0000-0000-0000-00000000dc01'
  AND namespace IN (SELECT namespace FROM offline_seed_namespaces);
DELETE FROM knowledge_documents WHERE file_id IN (SELECT id FROM offline_seed_files);
DELETE FROM knowledge_index_versions
WHERE workspace_id = '00000000-0000-0000-0000-00000000dc01'
  AND qdrant_namespace IN (SELECT namespace FROM offline_seed_namespaces);
DELETE FROM knowledge_bases
WHERE workspace_id = '00000000-0000-0000-0000-00000000dc01'
  AND namespace IN (SELECT namespace FROM offline_seed_namespaces);
DELETE FROM files WHERE id IN (SELECT id FROM offline_seed_files);
COMMIT;
