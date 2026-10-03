package jobs

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"

	"github.com/google/uuid"
)

func nonNilUUIDString(id uuid.UUID) string {
	if id == uuid.Nil {
		return ""
	}
	return id.String()
}

func freezeBuildSnapshot(payload ingestKnowledgePythonPayload, workspaceID uuid.UUID) (string, error) {
	if workspaceID == uuid.Nil || payload.IndexVersionID == uuid.Nil || payload.IngestionJobID == uuid.Nil {
		return "", errors.New("frozen build requires nonzero workspace/version/ingestion UUIDs")
	}
	if id, err := uuid.Parse(payload.KnowledgeBaseID); err != nil || id == uuid.Nil {
		return "", errors.New("frozen build requires a KB UUID")
	}
	data := []byte(payload.InputSnapshotJSON)
	digest := sha256.Sum256(data)
	if hex.EncodeToString(digest[:]) != payload.InputSnapshotHash {
		return "", errors.New("frozen build snapshot hash mismatch")
	}
	var snapshot map[string]json.RawMessage
	if err := json.Unmarshal(data, &snapshot); err != nil {
		return "", err
	}
	expected := map[string]string{"schema_version": "kb-build-input-v1", "workspace_id": workspaceID.String(),
		"knowledge_base_id": payload.KnowledgeBaseID, "index_version_id": payload.IndexVersionID.String(),
		"namespace": payload.Namespace, "collection": payload.QdrantCollection}
	for key, value := range expected {
		var actual string
		if json.Unmarshal(snapshot[key], &actual) != nil || value == "" || actual != value {
			return "", fmt.Errorf("frozen build snapshot %s mismatch", key)
		}
	}
	if payload.QdrantNamespace != "" && payload.QdrantNamespace != payload.Namespace {
		return "", errors.New("candidate namespace override mismatch")
	}
	path := filepath.Join(payload.OutDir, "input_snapshot.json")
	return path, persistFrozenBytes(path, data)
}

func freezeFillScopes(payload fillFormPythonPayload, workspaceID uuid.UUID) (string, error) {
	hasTarget := len(payload.TargetScope) > 0 && string(payload.TargetScope) != "null"
	hasGlobal := len(payload.GlobalScope) > 0 && string(payload.GlobalScope) != "null"
	hasTemplate := len(payload.TemplatePin) > 0 && string(payload.TemplatePin) != "null"
	if !hasTarget && !hasGlobal && !hasTemplate && payload.IndexScopesJSON == "" {
		return "", nil // persisted historical jobs retain the explicit legacy path.
	}
	if !hasTarget || !hasGlobal || !hasTemplate || payload.IndexScopesJSON == "" {
		return "", errors.New("fill task requires both frozen scopes and a template pin")
	}
	var pins []map[string]any
	if err := json.Unmarshal([]byte(payload.IndexScopesJSON), &pins); err != nil {
		return "", err
	}
	if len(pins) != 2 {
		return "", errors.New("fill task requires exactly two frozen scopes")
	}
	var target, global map[string]any
	if err := json.Unmarshal(payload.TargetScope, &target); err != nil {
		return "", err
	}
	if err := json.Unmarshal(payload.GlobalScope, &global); err != nil {
		return "", err
	}
	if !reflect.DeepEqual(pins, []map[string]any{target, global}) {
		return "", errors.New("index_scopes_json differs from the frozen target/global scopes")
	}
	for _, pin := range pins {
		for _, key := range []string{"namespace", "collection"} {
			if value, ok := pin[key].(string); !ok || value == "" {
				return "", fmt.Errorf("invalid scope %s", key)
			}
		}
		for _, key := range []string{"knowledge_base_id", "index_version_id"} {
			value, ok := pin[key].(string)
			id, err := uuid.Parse(value)
			if !ok || err != nil || id == uuid.Nil {
				return "", fmt.Errorf("invalid scope %s", key)
			}
		}
		if pin["storage_contract"] != "versioned_v1" && pin["storage_contract"] != "legacy_unversioned" {
			return "", errors.New("invalid index storage contract")
		}
	}
	if target["namespace"] != payload.TargetNamespace || global["namespace"] != payload.GlobalNamespace ||
		target["namespace"] == global["namespace"] || target["collection"] != global["collection"] {
		return "", errors.New("fill task namespace/collection scope mismatch")
	}
	var template map[string]any
	if err := json.Unmarshal(payload.TemplatePin, &template); err != nil {
		return "", err
	}
	if template["workspace_id"] != workspaceID.String() {
		return "", errors.New("frozen template workspace mismatch")
	}
	path := filepath.Join(payload.OutDir, "index_scopes.json")
	return path, persistFrozenBytes(path, []byte(payload.IndexScopesJSON))
}

// Called only after freezeFillScopes has verified the two scopes.
func frozenFillCollection(payload fillFormPythonPayload) string {
	var scope map[string]any
	if json.Unmarshal(payload.TargetScope, &scope) != nil {
		return ""
	}
	value, _ := scope["collection"].(string)
	return value
}

func persistFrozenBytes(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".frozen-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if _, err := file.Write(data); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Rename(file.Name(), path)
}

func effectiveFillResume(outDir string, requested bool) (bool, error) {
	if !requested {
		return false, nil
	}
	if _, err := os.Stat(filepath.Join(outDir, "form_input_snapshot.json")); err == nil {
		return true, nil
	} else if !os.IsNotExist(err) {
		return false, err
	}
	for _, name := range []string{"predictions.checkpoint.jsonl", "retrieval_evidence.checkpoint.jsonl"} {
		if _, err := os.Stat(filepath.Join(outDir, name)); err == nil {
			return false, errors.New("cannot resume: checkpoint has no frozen form input")
		} else if !os.IsNotExist(err) {
			return false, err
		}
	}
	return false, nil // first dispatch of a retry-enabled task.
}
