package knowledge

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
)

const (
	BuildInputSchemaVersion = "kb-build-input-v1"
	ValidationSchemaVersion = "kb-index-validation-v1"
	StorageVersioned        = "versioned_v1"
	StorageLegacy           = "legacy_unversioned"
)

type IndexScope struct {
	Collection      string    `json:"collection"`
	Namespace       string    `json:"namespace"`
	KnowledgeBaseID uuid.UUID `json:"knowledge_base_id"`
	IndexVersionID  uuid.UUID `json:"index_version_id"`
	StorageContract string    `json:"storage_contract"`
}

type BuildInputDocument struct {
	DocumentID   uuid.UUID `json:"document_id"`
	FileID       uuid.UUID `json:"file_id"`
	Filename     string    `json:"filename"`
	RelativePath string    `json:"relative_path"`
	ObjectKey    string    `json:"object_key"`
	SHA256       string    `json:"sha256"`
	SizeBytes    int64     `json:"size_bytes"`
	DocumentRole string    `json:"document_role"`
}

type BuildInputSnapshot struct {
	SchemaVersion   string               `json:"schema_version"`
	WorkspaceID     uuid.UUID            `json:"workspace_id"`
	KnowledgeBaseID uuid.UUID            `json:"knowledge_base_id"`
	IndexVersionID  uuid.UUID            `json:"index_version_id"`
	Collection      string               `json:"collection"`
	Namespace       string               `json:"namespace"`
	Documents       []BuildInputDocument `json:"documents"`
}

type ValidationReceipt struct {
	SchemaVersion         string    `json:"schema_version"`
	IndexVersionID        uuid.UUID `json:"index_version_id"`
	KnowledgeBaseID       uuid.UUID `json:"knowledge_base_id"`
	Namespace             string    `json:"namespace"`
	Collection            string    `json:"collection"`
	InputSnapshotHash     string    `json:"input_snapshot_hash"`
	ExpectedEvidenceCount int64     `json:"expected_evidence_count"`
	ActualEvidenceCount   int64     `json:"actual_evidence_count"`
	ExpectedSchemaCount   int64     `json:"expected_schema_count"`
	ActualSchemaCount     int64     `json:"actual_schema_count"`
	DocumentCount         int64     `json:"document_count"`
	SourceHashesVerified  bool      `json:"source_hashes_verified"`
	SmokePassed           bool      `json:"smoke_passed"`
	ValidatedAt           time.Time `json:"validated_at"`
}

// CanonicalSnapshotBytes sorts object keys and preserves integer precision.
// The returned bytes have no trailing newline. Python hashes the file bytes
// directly; it must not re-encode JSON before checking this hash.
func CanonicalSnapshotBytes(value any) ([]byte, error) {
	data, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var normalized any
	if err := decoder.Decode(&normalized); err != nil {
		return nil, err
	}
	return json.Marshal(normalized)
}

func SnapshotBytesHash(data []byte) string {
	digest := sha256.Sum256(data)
	return hex.EncodeToString(digest[:])
}

func SnapshotHash(value any) (string, error) {
	data, err := CanonicalSnapshotBytes(value)
	if err != nil {
		return "", err
	}
	return SnapshotBytesHash(data), nil
}

func (r ValidationReceipt) Validate(snapshot BuildInputSnapshot, hash string) error {
	if r.SchemaVersion != ValidationSchemaVersion || snapshot.SchemaVersion != BuildInputSchemaVersion ||
		r.IndexVersionID == uuid.Nil || r.IndexVersionID != snapshot.IndexVersionID ||
		r.KnowledgeBaseID == uuid.Nil || r.KnowledgeBaseID != snapshot.KnowledgeBaseID ||
		r.Namespace != snapshot.Namespace || r.Collection != snapshot.Collection ||
		r.InputSnapshotHash != hash {
		return fmt.Errorf("KB_VALIDATION_SCOPE: receipt does not match frozen build input")
	}
	if r.ExpectedEvidenceCount <= 0 || r.ActualEvidenceCount != r.ExpectedEvidenceCount ||
		r.ExpectedSchemaCount < 0 || r.ActualSchemaCount != r.ExpectedSchemaCount ||
		r.DocumentCount != int64(len(snapshot.Documents)) || len(snapshot.Documents) == 0 ||
		!r.SourceHashesVerified || !r.SmokePassed || r.ValidatedAt.IsZero() {
		return fmt.Errorf("KB_VALIDATION_FAILED: count, source hash or smoke validation failed")
	}
	return nil
}
