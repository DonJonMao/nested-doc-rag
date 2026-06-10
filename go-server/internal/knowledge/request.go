package knowledge

import (
	"io"

	"github.com/google/uuid"
)

type CreateKnowledgeBaseRequest struct {
	WorkspaceID      uuid.UUID `json:"workspace_id"`
	Name             string    `json:"name"`
	Description      string    `json:"description"`
	QdrantCollection string    `json:"qdrant_collection"`
}

type SetCurrentIndexVersionRequest struct {
	IndexVersionID uuid.UUID `json:"index_version_id"`
}

type UploadDocumentRequest struct {
	KnowledgeBaseID  uuid.UUID
	OriginalFilename string
	Size             int64
	MIMEType         string
	Reader           io.Reader
	DocumentRole     string
	Namespace        string
}

type CreateIngestionRunRequest struct {
	KnowledgeBaseID  uuid.UUID `json:"knowledge_base_id"`
	Namespace        string    `json:"namespace"`
	Rebuild          bool      `json:"rebuild"`
	QdrantCollection string    `json:"qdrant_collection"`
	QdrantNamespace  string    `json:"qdrant_namespace"`
	Resume           bool      `json:"resume"`
}
