package form

import (
	"io"

	"github.com/google/uuid"
)

type UploadFormRequest struct {
	WorkspaceID      uuid.UUID
	OriginalFilename string
	Size             int64
	MIMEType         string
	Reader           io.Reader
}

type CreateFillRunRequest struct {
	WorkspaceID uuid.UUID `json:"workspace_id"`
	FormFileID  uuid.UUID `json:"form_file_id"`
	Name        string    `json:"name"`

	KnowledgeBaseID *uuid.UUID `json:"knowledge_base_id"`
	IndexVersionID  *uuid.UUID `json:"index_version_id"`

	TargetNamespace string `json:"target_namespace"`
	GlobalNamespace string `json:"global_namespace"`
	RoomContext     string `json:"room_context"`
	Rows            string `json:"rows"`

	RetrievalMode string `json:"retrieval_mode"`
	PromptVersion string `json:"prompt_version"`
	Judge         bool   `json:"judge"`
	UseJudgeCache bool   `json:"use_judge_cache"`
	Writeback     *bool  `json:"writeback"`
}

type CreateSimpleFillRunRequest struct {
	WorkspaceID     uuid.UUID `json:"workspace_id"`
	KnowledgeBaseID uuid.UUID `json:"knowledge_base_id"`
	FormFileID      uuid.UUID `json:"form_file_id"`
	Name            string    `json:"name"`
	RoomContext     string    `json:"room_context"`
}
