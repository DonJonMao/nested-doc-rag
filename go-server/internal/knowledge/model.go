package knowledge

import (
	"time"

	"github.com/google/uuid"
)

type KnowledgeBase struct {
	ID                    uuid.UUID  `json:"id"`
	WorkspaceID           uuid.UUID  `json:"workspace_id"`
	Name                  string     `json:"name"`
	Namespace             string     `json:"namespace"`
	Description           string     `json:"description,omitempty"`
	QdrantCollection      string     `json:"qdrant_collection,omitempty"`
	CurrentIndexVersionID *uuid.UUID `json:"current_index_version_id,omitempty"`
	Status                string     `json:"status"`
	DocumentCount         int        `json:"document_count"`
	LastIngestedAt        *time.Time `json:"last_ingested_at,omitempty"`
	CreatedBy             uuid.UUID  `json:"created_by"`
	CreatedAt             time.Time  `json:"created_at"`
	UpdatedAt             time.Time  `json:"updated_at"`
}

type KnowledgeDocument struct {
	ID              uuid.UUID  `json:"id"`
	KnowledgeBaseID uuid.UUID  `json:"knowledge_base_id"`
	WorkspaceID     uuid.UUID  `json:"workspace_id"`
	FileID          uuid.UUID  `json:"file_id"`
	Filename        string     `json:"filename"`
	DocumentRole    string     `json:"document_role"`
	Namespace       string     `json:"namespace"`
	Status          string     `json:"status"`
	CreatedBy       uuid.UUID  `json:"created_by"`
	CreatedAt       time.Time  `json:"created_at"`
	UpdatedAt       time.Time  `json:"updated_at"`
	DeletedAt       *time.Time `json:"deleted_at,omitempty"`
	LastIngestedAt  *time.Time `json:"last_ingested_at,omitempty"`
}

type KnowledgeIndexVersion struct {
	ID               uuid.UUID  `json:"id"`
	KnowledgeBaseID  uuid.UUID  `json:"knowledge_base_id"`
	WorkspaceID      uuid.UUID  `json:"workspace_id"`
	Version          int        `json:"version"`
	QdrantCollection string     `json:"qdrant_collection"`
	QdrantNamespace  string     `json:"qdrant_namespace,omitempty"`
	ArtifactDir      string     `json:"artifact_dir,omitempty"`
	ManifestPath     string     `json:"manifest_path,omitempty"`
	Status           string     `json:"status"`
	DocumentCount    int        `json:"document_count"`
	ChunkCount       int        `json:"chunk_count"`
	CreatedBy        uuid.UUID  `json:"created_by"`
	CreatedAt        time.Time  `json:"created_at"`
	ReadyAt          *time.Time `json:"ready_at,omitempty"`
	FailedAt         *time.Time `json:"failed_at,omitempty"`
	ErrorMessage     string     `json:"error_message,omitempty"`
}

type IngestionJob struct {
	ID              uuid.UUID  `json:"id"`
	WorkspaceID     uuid.UUID  `json:"workspace_id"`
	KnowledgeBaseID uuid.UUID  `json:"knowledge_base_id"`
	IndexVersionID  *uuid.UUID `json:"index_version_id,omitempty"`
	JobID           *uuid.UUID `json:"job_id,omitempty"`
	Status          string     `json:"status"`
	Progress        int        `json:"progress"`
	DocumentCount   int        `json:"document_count"`
	ErrorMessage    string     `json:"error_message,omitempty"`
	PythonCommand   string     `json:"python_command,omitempty"`
	OutDir          string     `json:"out_dir,omitempty"`
	StartedAt       *time.Time `json:"started_at,omitempty"`
	FinishedAt      *time.Time `json:"finished_at,omitempty"`
	CreatedBy       uuid.UUID  `json:"created_by"`
	CreatedAt       time.Time  `json:"created_at"`
	UpdatedAt       time.Time  `json:"updated_at"`
}
