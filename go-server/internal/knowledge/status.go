package knowledge

const (
	DocumentRoleKnowledgeBase   = "knowledge_base"
	DocumentRoleIntroDoc        = "intro_doc"
	DocumentRoleProofAttachment = "proof_attachment"
	DocumentRoleMisc            = "misc"

	KnowledgeDocumentStatusUploaded = "uploaded"
	KnowledgeDocumentStatusIndexing = "indexing"
	KnowledgeDocumentStatusIndexed  = "indexed"
	KnowledgeDocumentStatusFailed   = "failed"
	KnowledgeDocumentStatusDeleted  = "deleted"

	KnowledgeBaseStatusEmpty    = "empty"
	KnowledgeBaseStatusBuilding = "building"
	KnowledgeBaseStatusReady    = "ready"
	KnowledgeBaseStatusStale    = "stale"
	KnowledgeBaseStatusFailed   = "failed"
	KnowledgeBaseStatusArchived = "archived"

	IndexVersionStatusBuilding = "building"
	IndexVersionStatusReady    = "ready"
	IndexVersionStatusFailed   = "failed"
	IndexVersionStatusArchived = "archived"

	IngestionJobStatusCreated         = "created"
	IngestionJobStatusQueued          = "queued"
	IngestionJobStatusRunning         = "running"
	IngestionJobStatusSucceeded       = "succeeded"
	IngestionJobStatusFailed          = "failed"
	IngestionJobStatusCanceled        = "canceled"
	IngestionJobStatusCancelRequested = "cancel_requested"
	IngestionJobStatusDisabled        = "disabled"
)

func ValidDocumentRole(role string) bool {
	switch role {
	case DocumentRoleKnowledgeBase, DocumentRoleIntroDoc, DocumentRoleProofAttachment, DocumentRoleMisc:
		return true
	default:
		return false
	}
}

func ValidKnowledgeBaseStatus(status string) bool {
	switch status {
	case KnowledgeBaseStatusEmpty, KnowledgeBaseStatusBuilding, KnowledgeBaseStatusReady, KnowledgeBaseStatusStale, KnowledgeBaseStatusFailed, KnowledgeBaseStatusArchived:
		return true
	default:
		return false
	}
}

func ValidKnowledgeDocumentStatus(status string) bool {
	switch status {
	case KnowledgeDocumentStatusUploaded, KnowledgeDocumentStatusIndexing, KnowledgeDocumentStatusIndexed, KnowledgeDocumentStatusFailed, KnowledgeDocumentStatusDeleted:
		return true
	default:
		return false
	}
}

func ValidIndexVersionStatus(status string) bool {
	switch status {
	case IndexVersionStatusBuilding, IndexVersionStatusReady, IndexVersionStatusFailed, IndexVersionStatusArchived:
		return true
	default:
		return false
	}
}

func ValidIngestionJobStatus(status string) bool {
	switch status {
	case IngestionJobStatusCreated, IngestionJobStatusQueued, IngestionJobStatusRunning, IngestionJobStatusSucceeded, IngestionJobStatusFailed, IngestionJobStatusCanceled, IngestionJobStatusCancelRequested, IngestionJobStatusDisabled:
		return true
	default:
		return false
	}
}
