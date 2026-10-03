package form

import (
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
)

type FormFile struct {
	ID          uuid.UUID `json:"id"`
	WorkspaceID uuid.UUID `json:"workspace_id"`
	FileID      uuid.UUID `json:"file_id"`
	Filename    string    `json:"filename"`
	CreatedBy   uuid.UUID `json:"created_by"`
	CreatedAt   time.Time `json:"created_at"`
}

type FillRun struct {
	ID          uuid.UUID  `json:"id"`
	WorkspaceID uuid.UUID  `json:"workspace_id"`
	FormFileID  uuid.UUID  `json:"form_file_id"`
	JobID       *uuid.UUID `json:"job_id,omitempty"`
	Name        string     `json:"name"`

	KnowledgeBaseID          *uuid.UUID            `json:"knowledge_base_id,omitempty"`
	IndexVersionID           *uuid.UUID            `json:"index_version_id,omitempty"`
	TargetScope              *knowledge.IndexScope `json:"target_scope,omitempty"`
	GlobalScope              *knowledge.IndexScope `json:"global_scope,omitempty"`
	TargetActivationRevision *int64                `json:"target_activation_revision,omitempty"`
	GlobalActivationRevision *int64                `json:"global_activation_revision,omitempty"`
	TemplatePin              *TemplatePin          `json:"template_pin,omitempty"`

	TargetNamespace string `json:"target_namespace"`
	GlobalNamespace string `json:"global_namespace"`
	RoomContext     string `json:"room_context,omitempty"`
	RowsSpec        string `json:"rows"`

	RetrievalMode    string `json:"retrieval_mode"`
	PromptVersion    string `json:"prompt_version"`
	JudgeEnabled     bool   `json:"judge_enabled"`
	UseJudgeCache    bool   `json:"use_judge_cache"`
	WritebackEnabled bool   `json:"writeback_enabled"`

	Status string `json:"status"`

	ProgressTotal int `json:"progress_total"`
	ProgressDone  int `json:"progress_done"`

	OutDir               string     `json:"out_dir,omitempty"`
	RunManifestPath      string     `json:"run_manifest_path,omitempty"`
	SummaryPath          string     `json:"summary_path,omitempty"`
	FilledFormArtifactID *uuid.UUID `json:"filled_form_artifact_id,omitempty"`

	AnsweredCount           int `json:"answered_count"`
	PartialClueCount        int `json:"partial_clue_count"`
	NotFoundCount           int `json:"not_found_count"`
	ConflictUnresolvedCount int `json:"conflict_unresolved_count"`
	ReviewRequiredCount     int `json:"review_required_count"`
	WritebackAllowedCount   int `json:"writeback_allowed_count"`
	FailedCount             int `json:"failed_count"`

	ErrorMessage string `json:"error_message,omitempty"`

	CreatedBy  uuid.UUID  `json:"created_by"`
	CreatedAt  time.Time  `json:"created_at"`
	QueuedAt   *time.Time `json:"queued_at,omitempty"`
	StartedAt  *time.Time `json:"started_at,omitempty"`
	FinishedAt *time.Time `json:"finished_at,omitempty"`
	UpdatedAt  time.Time  `json:"updated_at"`
}

// TemplatePin identifies the immutable input object chosen when a run is created.
type TemplatePin struct {
	WorkspaceID uuid.UUID `json:"workspace_id"`
	FileID      uuid.UUID `json:"file_id"`
	ObjectKey   string    `json:"object_key"`
	SHA256      string    `json:"sha256"`
	FileSize    int64     `json:"file_size"`
	Filename    string    `json:"filename"`
}

type FillRunCompletionUpdate struct {
	RunManifestPath         string
	SummaryPath             string
	FilledFormArtifactID    *uuid.UUID
	ProgressTotal           int
	ProgressDone            int
	AnsweredCount           int
	PartialClueCount        int
	NotFoundCount           int
	ConflictUnresolvedCount int
	ReviewRequiredCount     int
	WritebackAllowedCount   int
	FailedCount             int
}
