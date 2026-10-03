-- +goose Up
ALTER TABLE knowledge_bases
 ADD COLUMN source_revision bigint NOT NULL DEFAULT 0 CHECK(source_revision>=0),
 ADD COLUMN source_dirty boolean NOT NULL DEFAULT false,
 ADD COLUMN activation_revision bigint NOT NULL DEFAULT 0 CHECK(activation_revision>=0),
 ADD CONSTRAINT uq_knowledge_bases_owner UNIQUE(id,workspace_id);
ALTER TABLE files ADD CONSTRAINT uq_files_owner UNIQUE(id,workspace_id);
ALTER TABLE knowledge_index_versions
 ADD COLUMN storage_contract text NOT NULL DEFAULT 'legacy_unversioned' CHECK(storage_contract IN ('legacy_unversioned','versioned_v1')),
 ADD COLUMN validation_state text NOT NULL DEFAULT 'unvalidated' CHECK(validation_state IN ('unvalidated','legacy_declared_ready','pending','validated','failed')),
 ADD COLUMN input_snapshot_json jsonb,
 ADD COLUMN input_snapshot_canonical text,
 ADD COLUMN input_snapshot_hash text CHECK(input_snapshot_hash ~ '^[0-9a-f]{64}$'),
 ADD COLUMN source_revision bigint NOT NULL DEFAULT 0 CHECK(source_revision>=0),
 ADD COLUMN expected_active_version_id uuid,
 ADD COLUMN expected_activation_revision bigint NOT NULL DEFAULT 0 CHECK(expected_activation_revision>=0),
 ADD COLUMN validation_receipt_json jsonb,
 ADD COLUMN publication_state text NOT NULL DEFAULT 'unpublished' CHECK(publication_state IN ('unpublished','activated','superseded')),
 ADD CONSTRAINT uq_knowledge_index_versions_owner UNIQUE(id,knowledge_base_id,workspace_id),
 ADD CONSTRAINT fk_knowledge_index_versions_owner FOREIGN KEY(knowledge_base_id,workspace_id) REFERENCES knowledge_bases(id,workspace_id),
 ADD CONSTRAINT ck_knowledge_version_validation CHECK(validation_state<>'validated' OR (storage_contract='versioned_v1' AND status='ready' AND input_snapshot_json IS NOT NULL AND input_snapshot_canonical IS NOT NULL AND input_snapshot_hash IS NOT NULL AND validation_receipt_json IS NOT NULL));
UPDATE knowledge_index_versions SET validation_state='legacy_declared_ready' WHERE status='ready';
ALTER TABLE knowledge_bases ADD CONSTRAINT fk_knowledge_bases_current_owner
 FOREIGN KEY(current_index_version_id,id,workspace_id) REFERENCES knowledge_index_versions(id,knowledge_base_id,workspace_id) DEFERRABLE INITIALLY IMMEDIATE;
ALTER TABLE fill_runs
 ADD COLUMN target_scope_json jsonb,
 ADD COLUMN global_scope_json jsonb,
 ADD COLUMN target_activation_revision bigint,
 ADD COLUMN global_activation_revision bigint,
 ADD CONSTRAINT uq_fill_runs_owner UNIQUE(id,workspace_id);
CREATE TABLE knowledge_version_source_pins (
 index_version_id uuid NOT NULL,
 document_id uuid NOT NULL REFERENCES knowledge_documents(id) ON DELETE RESTRICT,
 file_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 knowledge_base_id uuid NOT NULL,
 filename text NOT NULL,
 relative_path text NOT NULL,
 object_key text NOT NULL,
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 size_bytes bigint NOT NULL CHECK(size_bytes>0),
 document_role text NOT NULL,
 PRIMARY KEY(index_version_id,document_id),
 FOREIGN KEY(index_version_id,knowledge_base_id,workspace_id) REFERENCES knowledge_index_versions(id,knowledge_base_id,workspace_id) ON DELETE RESTRICT,
 FOREIGN KEY(file_id,workspace_id) REFERENCES files(id,workspace_id) ON DELETE RESTRICT
);
CREATE INDEX idx_knowledge_version_source_pins_file ON knowledge_version_source_pins(file_id);
CREATE TABLE fill_run_index_pins (
 run_id uuid NOT NULL,
 workspace_id uuid NOT NULL,
 role text NOT NULL CHECK(role IN ('target','global')),
 knowledge_base_id uuid NOT NULL,
 index_version_id uuid NOT NULL,
 activation_revision bigint NOT NULL CHECK(activation_revision>=0),
 PRIMARY KEY(run_id,role),
 FOREIGN KEY(run_id,workspace_id) REFERENCES fill_runs(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(index_version_id,knowledge_base_id,workspace_id) REFERENCES knowledge_index_versions(id,knowledge_base_id,workspace_id) ON DELETE RESTRICT
);
CREATE INDEX idx_fill_run_index_pins_version ON fill_run_index_pins(index_version_id);
CREATE TABLE fill_run_template_pins (
 run_id uuid PRIMARY KEY,
 workspace_id uuid NOT NULL,
 file_id uuid NOT NULL,
 object_key text NOT NULL,
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 file_size bigint NOT NULL CHECK(file_size>0),
 filename text NOT NULL,
 FOREIGN KEY(run_id,workspace_id) REFERENCES fill_runs(id,workspace_id) ON DELETE CASCADE,
 FOREIGN KEY(file_id,workspace_id) REFERENCES files(id,workspace_id) ON DELETE RESTRICT
);
CREATE INDEX idx_fill_run_template_pins_file ON fill_run_template_pins(file_id);

CREATE FUNCTION gongkan_guard_version_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.id,NEW.knowledge_base_id,NEW.workspace_id,NEW.version,NEW.qdrant_collection,NEW.qdrant_namespace,NEW.storage_contract,
  NEW.input_snapshot_json,NEW.input_snapshot_canonical,NEW.input_snapshot_hash,NEW.source_revision,NEW.expected_active_version_id,NEW.expected_activation_revision)
 IS DISTINCT FROM ROW(OLD.id,OLD.knowledge_base_id,OLD.workspace_id,OLD.version,OLD.qdrant_collection,OLD.qdrant_namespace,OLD.storage_contract,
  OLD.input_snapshot_json,OLD.input_snapshot_canonical,OLD.input_snapshot_hash,OLD.source_revision,OLD.expected_active_version_id,OLD.expected_activation_revision) THEN
  RAISE EXCEPTION 'immutable knowledge version identity/input cannot change';
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER knowledge_version_identity_immutable BEFORE UPDATE ON knowledge_index_versions FOR EACH ROW EXECUTE FUNCTION gongkan_guard_version_identity();
CREATE FUNCTION gongkan_guard_pin_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'immutable version/template pin cannot change'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER knowledge_source_pin_immutable BEFORE UPDATE ON knowledge_version_source_pins FOR EACH ROW EXECUTE FUNCTION gongkan_guard_pin_update();
CREATE TRIGGER fill_index_pin_immutable BEFORE UPDATE ON fill_run_index_pins FOR EACH ROW EXECUTE FUNCTION gongkan_guard_pin_update();
CREATE TRIGGER fill_template_pin_immutable BEFORE UPDATE ON fill_run_template_pins FOR EACH ROW EXECUTE FUNCTION gongkan_guard_pin_update();
CREATE FUNCTION gongkan_guard_fill_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.target_scope_json IS NOT NULL AND ROW(NEW.target_scope_json,NEW.global_scope_json,NEW.target_activation_revision,NEW.global_activation_revision,NEW.knowledge_base_id,NEW.index_version_id,NEW.workspace_id)
 IS DISTINCT FROM ROW(OLD.target_scope_json,OLD.global_scope_json,OLD.target_activation_revision,OLD.global_activation_revision,OLD.knowledge_base_id,OLD.index_version_id,OLD.workspace_id) THEN
  RAISE EXCEPTION 'immutable fill scope cannot change';
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER fill_scope_immutable BEFORE UPDATE ON fill_runs FOR EACH ROW EXECUTE FUNCTION gongkan_guard_fill_scope();

-- +goose Down
-- Versioned publication and immutable pins require a forward repair migration.
SELECT 1;
