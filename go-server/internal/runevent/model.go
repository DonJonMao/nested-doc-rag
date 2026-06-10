package runevent

import (
	"time"

	"github.com/google/uuid"
)

const (
	EventQueued                      = "queued"
	EventRunning                     = "running"
	EventHeartbeat                   = "heartbeat"
	EventProgress                    = "progress"
	EventCheckpointWritten           = "checkpoint_written"
	EventSucceeded                   = "succeeded"
	EventCompletedWithFailures       = "completed_with_failures"
	EventFailed                      = "failed"
	EventCanceled                    = "canceled"
	EventCancelRequested             = "cancel_requested"
	EventReviewItemCreated           = "review_item_created"
	EventWritebackCompleted          = "writeback_completed"
	EventPythonStarted               = "python_started"
	EventPythonFinished              = "python_finished"
	EventArtifactValidationSucceeded = "artifact_validation_succeeded"
	EventArtifactValidationFailed    = "artifact_validation_failed"
	EventArtifactsRegistered         = "artifacts_registered"
	EventIngestionStarted            = "ingestion_started"
	EventIngestionMaterialized       = "ingestion_materialized"
	EventIngestionFinished           = "ingestion_finished"
	EventIngestionFailed             = "ingestion_failed"
	EventIndexVersionReady           = "index_version_ready"
)

type RunEvent struct {
	ID          uuid.UUID      `json:"id"`
	WorkspaceID uuid.UUID      `json:"workspace_id"`
	RunID       uuid.UUID      `json:"run_id"`
	JobID       *uuid.UUID     `json:"job_id,omitempty"`
	EventType   string         `json:"event_type"`
	Sequence    int64          `json:"sequence"`
	Payload     map[string]any `json:"payload"`
	CreatedAt   time.Time      `json:"created_at"`
}
