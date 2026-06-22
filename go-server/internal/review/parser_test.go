package review

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/google/uuid"
)

func TestArtifactReviewParserImportsWritebackEvidenceRefs(t *testing.T) {
	dir := t.TempDir()
	reviewPath := filepath.Join(dir, "review_items.jsonl")
	line := `{"field_id":"field_1","row_index":4,"target_cell":"Sheet1!C4","answer_status":"partial_clue","answer_value":"双路市电","status":"uncertain","writeback_action":"written_red_comment","error_code":"WB_POLICY_REJECTED","evidence_refs":[{"document_id":"doc_1","source_anchor":"能力清单!H42","image_object_key":"runs/run_1/evidence/field_1/proof.png"}]}`
	if err := os.WriteFile(reviewPath, []byte(line+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	parser := &ArtifactReviewParser{}
	items, err := parser.ParseReviewItems(context.Background(), uuid.New(), uuid.New(), ReviewArtifactPaths{ReviewItemsPath: reviewPath})
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 {
		t.Fatalf("expected one item, got %d", len(items))
	}
	item := items[0]
	if item.WritebackStatus != "uncertain" {
		t.Fatalf("WritebackStatus = %q", item.WritebackStatus)
	}
	if item.WritebackAction != "written_red_comment" {
		t.Fatalf("WritebackAction = %q", item.WritebackAction)
	}
	if item.WritebackErrorCode != "WB_POLICY_REJECTED" {
		t.Fatalf("WritebackErrorCode = %q", item.WritebackErrorCode)
	}
	if len(item.EvidenceRefs) != 1 {
		t.Fatalf("expected one evidence ref, got %d", len(item.EvidenceRefs))
	}
	if got := item.EvidenceRefs[0]["image_object_key"]; got != "runs/run_1/evidence/field_1/proof.png" {
		t.Fatalf("unexpected image_object_key: %v", got)
	}
}
