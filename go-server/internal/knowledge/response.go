package knowledge

type KnowledgeBaseListResponse struct {
	KnowledgeBases []KnowledgeBase `json:"knowledge_bases"`
}

type KnowledgeBaseOptionsResponse struct {
	KnowledgeBases []KnowledgeBase `json:"knowledge_bases"`
}

type KnowledgeDocumentListResponse struct {
	Documents []KnowledgeDocument `json:"documents"`
}

type UploadDocumentResponse struct {
	Document     *KnowledgeDocument     `json:"document"`
	IngestionJob *IngestionJob          `json:"ingestion_job,omitempty"`
	IndexVersion *KnowledgeIndexVersion `json:"index_version,omitempty"`
}

type DeleteDocumentResponse struct {
	Document     *KnowledgeDocument `json:"document"`
	IngestionJob *IngestionJob      `json:"ingestion_job,omitempty"`
	Deleted      bool               `json:"deleted"`
}

type IndexVersionListResponse struct {
	IndexVersions []KnowledgeIndexVersion `json:"index_versions"`
}

type IngestionJobListResponse struct {
	IngestionJobs []IngestionJob `json:"ingestion_jobs"`
}

type CancelIngestionJobResponse struct {
	IngestionJob *IngestionJob `json:"ingestion_job"`
	Canceled     bool          `json:"canceled"`
}
