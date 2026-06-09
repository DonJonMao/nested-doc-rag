package artifact

import (
	"io"
	"time"

	"github.com/google/uuid"
)

const (
	TypePredictionsRaw       = "predictions_raw"
	TypePredictions          = "predictions"
	TypeAgentOverlays        = "agent_overlays"
	TypePredictionsAgentView = "predictions_agent_view"
	TypeReviewItems          = "review_items"
	TypeTrace                = "trace"
	TypeTraceSummary         = "trace_summary"
	TypeRunSummary           = "run_summary"
	TypeSummary              = "summary"
	TypeRunManifest          = "run_manifest"
	TypeFilledForm           = "filled_form"
	TypeWritebackAudit       = "writeback_audit"
	TypeEvidenceMap          = "evidence_map"
)

type RunArtifact struct {
	ID           uuid.UUID `json:"id"`
	WorkspaceID  uuid.UUID `json:"workspace_id"`
	RunID        uuid.UUID `json:"run_id"`
	ArtifactType string    `json:"artifact_type"`
	Filename     string    `json:"filename"`
	ObjectKey    string    `json:"object_key,omitempty"`
	LocalPath    string    `json:"local_path,omitempty"`
	ContentType  string    `json:"content_type"`
	FileSize     int64     `json:"file_size"`
	SHA256       string    `json:"sha256"`
	CreatedBy    uuid.UUID `json:"created_by"`
	CreatedAt    time.Time `json:"created_at"`
}

type RegisterArtifactRequest struct {
	WorkspaceID  uuid.UUID
	RunID        uuid.UUID
	ArtifactType string
	Filename     string
	ObjectKey    string
	LocalPath    string
	ContentType  string
	FileSize     int64
	SHA256       string
	Reader       io.Reader
}

type DownloadResult struct {
	Filename      string
	ContentType   string
	ContentLength int64
	Reader        io.ReadCloser
	PresignedURL  string
}
