package knowledge

type KnowledgeBaseListResponse struct {
	KnowledgeBases []KnowledgeBase `json:"knowledge_bases"`
}

type KnowledgeDocumentListResponse struct {
	Documents []KnowledgeDocument `json:"documents"`
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
