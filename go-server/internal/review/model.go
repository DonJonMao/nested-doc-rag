package review

import (
	"time"

	"github.com/google/uuid"
)

type ReviewItem struct {
	ID          uuid.UUID `json:"id"`
	WorkspaceID uuid.UUID `json:"workspace_id"`
	RunID       uuid.UUID `json:"run_id"`

	FieldID      string `json:"field_id,omitempty"`
	RowIndex     int    `json:"row_index,omitempty"`
	TargetCell   string `json:"target_cell,omitempty"`
	QuestionText string `json:"question_text,omitempty"`

	AnswerStatus string  `json:"answer_status,omitempty"`
	AnswerValue  string  `json:"answer_value,omitempty"`
	Confidence   float64 `json:"confidence,omitempty"`

	SourceChunkIDs           []string         `json:"source_chunk_ids"`
	EvidenceAttachmentIDs    []string         `json:"evidence_attachment_ids"`
	ReferenceChunkIDs        []string         `json:"reference_chunk_ids"`
	ReferenceSourceDocuments []map[string]any `json:"reference_source_documents"`
	ReferenceSnippets        []string         `json:"reference_snippets"`

	CriticFlags                       []string         `json:"critic_flags"`
	RiskLevel                         string           `json:"risk_level"`
	ReviewRequired                    bool             `json:"review_required"`
	WritebackAllowed                  bool             `json:"writeback_allowed"`
	SuggestedStatus                   string           `json:"suggested_status,omitempty"`
	SuggestedAnswerValue              string           `json:"suggested_answer_value,omitempty"`
	SuggestedReferenceSourceDocuments []map[string]any `json:"suggested_reference_source_documents"`
	Reasons                           []string         `json:"reasons"`

	Status        string     `json:"status"`
	ReviewerID    *uuid.UUID `json:"reviewer_id,omitempty"`
	ReviewedAt    *time.Time `json:"reviewed_at,omitempty"`
	ReviewComment string     `json:"review_comment,omitempty"`
	EditedAnswer  string     `json:"edited_answer,omitempty"`

	RawPayload     map[string]any `json:"raw_payload,omitempty"`
	OverlayPayload map[string]any `json:"overlay_payload,omitempty"`

	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

type ReviewFilter struct {
	WorkspaceID      uuid.UUID
	Status           string
	RiskLevel        string
	ReviewRequired   *bool
	WritebackAllowed *bool
	Limit            int
	Offset           int
}

type ReviewCounts struct {
	Total            int `json:"total"`
	Pending          int `json:"pending"`
	Approved         int `json:"approved"`
	Rejected         int `json:"rejected"`
	Edited           int `json:"edited"`
	Ignored          int `json:"ignored"`
	Reopened         int `json:"reopened"`
	HighRisk         int `json:"high_risk"`
	ReviewRequired   int `json:"review_required"`
	WritebackAllowed int `json:"writeback_allowed"`
}

type ReviewStatusUpdate struct {
	Status        string
	ReviewerID    uuid.UUID
	ReviewComment string
	EditedAnswer  string
	ReviewedAt    time.Time
}

type ReviewImportResult struct {
	TotalParsed      int `json:"total_parsed"`
	Created          int `json:"created"`
	Updated          int `json:"updated"`
	Skipped          int `json:"skipped"`
	ParseErrors      int `json:"parse_errors"`
	ReviewRequired   int `json:"review_required"`
	WritebackAllowed int `json:"writeback_allowed"`
}
