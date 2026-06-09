package jobs

import (
	"time"

	"github.com/google/uuid"
)

const (
	JobStatusCreated               = "created"
	JobStatusQueued                = "queued"
	JobStatusRunning               = "running"
	JobStatusSucceeded             = "succeeded"
	JobStatusCompletedWithFailures = "completed_with_failures"
	JobStatusFailed                = "failed"
	JobStatusCanceled              = "canceled"
	JobStatusCancelRequested       = "cancel_requested"

	JobTypeNoop             = "noop"
	JobTypeIngestKnowledge  = "ingest_knowledge"
	JobTypeFillForm         = "fill_form"
	JobTypeArchiveArtifacts = "archive_artifacts"

	ResourceTypeNoop            = "noop"
	ResourceTypeKnowledgeBase   = "knowledge_base"
	ResourceTypeFillRun         = "fill_run"
	ResourceTypeArtifactArchive = "artifact_archive"
)

type Job struct {
	ID                uuid.UUID      `json:"id"`
	WorkspaceID       uuid.UUID      `json:"workspace_id"`
	JobType           string         `json:"job_type"`
	ResourceType      string         `json:"resource_type"`
	ResourceID        uuid.UUID      `json:"resource_id"`
	Status            string         `json:"status"`
	Priority          int            `json:"priority"`
	Attempt           int            `json:"attempt"`
	MaxAttempts       int            `json:"max_attempts"`
	Payload           map[string]any `json:"payload"`
	ErrorMessage      string         `json:"error_message,omitempty"`
	CancelRequestedAt *time.Time     `json:"cancel_requested_at,omitempty"`
	QueuedAt          *time.Time     `json:"queued_at,omitempty"`
	StartedAt         *time.Time     `json:"started_at,omitempty"`
	HeartbeatAt       *time.Time     `json:"heartbeat_at,omitempty"`
	FinishedAt        *time.Time     `json:"finished_at,omitempty"`
	CreatedBy         uuid.UUID      `json:"created_by"`
	CreatedAt         time.Time      `json:"created_at"`
	UpdatedAt         time.Time      `json:"updated_at"`
}

type CreateJobRequest struct {
	WorkspaceID  uuid.UUID      `json:"workspace_id"`
	JobType      string         `json:"job_type"`
	ResourceType string         `json:"resource_type"`
	ResourceID   uuid.UUID      `json:"resource_id"`
	Payload      map[string]any `json:"payload"`
	Priority     int            `json:"priority"`
	MaxAttempts  int            `json:"max_attempts"`
}

type NoopJobRequest struct {
	WorkspaceID uuid.UUID `json:"workspace_id"`
	SleepMS     int       `json:"sleep_ms"`
}

type UpdateStatusFields struct {
	ErrorMessage      string
	CancelRequestedAt *time.Time
	QueuedAt          *time.Time
	StartedAt         *time.Time
	HeartbeatAt       *time.Time
	FinishedAt        *time.Time
}

func ValidJobType(jobType string) bool {
	switch jobType {
	case JobTypeNoop, JobTypeIngestKnowledge, JobTypeFillForm, JobTypeArchiveArtifacts:
		return true
	default:
		return false
	}
}

func ValidResourceType(resourceType string) bool {
	switch resourceType {
	case ResourceTypeNoop, ResourceTypeKnowledgeBase, ResourceTypeFillRun, ResourceTypeArtifactArchive:
		return true
	default:
		return false
	}
}

func ValidJobStatus(status string) bool {
	switch status {
	case JobStatusCreated, JobStatusQueued, JobStatusRunning, JobStatusSucceeded, JobStatusCompletedWithFailures, JobStatusFailed, JobStatusCanceled, JobStatusCancelRequested:
		return true
	default:
		return false
	}
}

func IsTerminal(status string) bool {
	switch status {
	case JobStatusSucceeded, JobStatusCompletedWithFailures, JobStatusFailed, JobStatusCanceled:
		return true
	default:
		return false
	}
}
