package python

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadRunManifest11WritebackFields(t *testing.T) {
	dir := t.TempDir()
	manifestPath := filepath.Join(dir, RunManifestFilename)
	if err := os.WriteFile(manifestPath, []byte(`{
		"schema_version": "1.1",
		"run_id": "run_1",
		"status": "completed",
		"writeback_enabled": true,
		"artifacts": {
			"summary": "summary.json",
			"filled_form": "filled_form.xlsx",
			"writeback_audit": "writeback_audit.jsonl",
			"image_evidence": "image_evidence.jsonl"
		},
		"counts": {"total_fields": 1},
		"writeback": {
			"summary": {"confirmed": 0, "uncertain": 1, "flagged": 0, "written": 1, "review": 1},
			"fields": [{
				"field_key": "field_1",
				"field_id": "field_1",
				"row_index": 4,
				"target_cell": "Sheet1!C4",
				"sheet_name": "Sheet1",
				"cell": "C4",
				"status": "uncertain",
				"answer_status": "partial_clue",
				"answer_value": "双路市电",
				"writeback_action": "written_red_comment",
				"evidence_refs": [{"document_id": "doc_1", "image_object_key": "runs/run_1/evidence/field_1/proof.png"}]
			}]
		}
	}`), 0o644); err != nil {
		t.Fatal(err)
	}

	manifest, err := LoadRunManifest(manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	if manifest.SchemaVersion != "1.1" {
		t.Fatalf("schema version = %q", manifest.SchemaVersion)
	}
	if manifest.Writeback.Summary.Uncertain != 1 || manifest.Writeback.Summary.Written != 1 {
		t.Fatalf("unexpected writeback summary: %+v", manifest.Writeback.Summary)
	}
	if len(manifest.Writeback.Fields) != 1 {
		t.Fatalf("expected one field, got %d", len(manifest.Writeback.Fields))
	}
	field := manifest.Writeback.Fields[0]
	if field.Status != "uncertain" || field.WritebackAction != "written_red_comment" {
		t.Fatalf("unexpected writeback field: %+v", field)
	}
	if got := field.EvidenceRefs[0]["image_object_key"]; got != "runs/run_1/evidence/field_1/proof.png" {
		t.Fatalf("unexpected image_object_key: %v", got)
	}
}
