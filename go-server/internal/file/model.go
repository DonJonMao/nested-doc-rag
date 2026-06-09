package file

import (
	"io"
	"time"

	"github.com/google/uuid"
)

const (
	FileCategoryKnowledgeDocument = "knowledge_document"
	FileCategoryFormTemplate      = "form_template"
	FileCategoryProofAttachment   = "proof_attachment"
	FileCategoryMisc              = "misc"

	FileStatusActive      = "active"
	FileStatusDeleted     = "deleted"
	FileStatusQuarantined = "quarantined"
)

type File struct {
	ID               uuid.UUID  `json:"id"`
	WorkspaceID      uuid.UUID  `json:"workspace_id"`
	Filename         string     `json:"filename"`
	OriginalFilename string     `json:"original_filename"`
	ObjectKey        string     `json:"object_key,omitempty"`
	FileSize         int64      `json:"file_size"`
	MIMEType         string     `json:"mime_type"`
	SHA256           string     `json:"sha256"`
	FileCategory     string     `json:"file_category"`
	Status           string     `json:"status"`
	CreatedBy        uuid.UUID  `json:"created_by"`
	CreatedAt        time.Time  `json:"created_at"`
	DeletedAt        *time.Time `json:"deleted_at,omitempty"`
}

type UploadFileRequest struct {
	WorkspaceID      uuid.UUID
	OriginalFilename string
	Size             int64
	MIMEType         string
	Category         string
	Reader           io.Reader
}

type DownloadResult struct {
	Filename      string
	ContentType   string
	ContentLength int64
	Reader        io.ReadCloser
}

func ValidCategory(category string) bool {
	switch category {
	case FileCategoryKnowledgeDocument, FileCategoryFormTemplate, FileCategoryProofAttachment, FileCategoryMisc:
		return true
	default:
		return false
	}
}
