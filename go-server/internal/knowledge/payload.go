package knowledge

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/google/uuid"
)

type IngestKnowledgeJobPayload struct {
	IngestionJobID  uuid.UUID `json:"ingestion_job_id"`
	WorkspaceID     uuid.UUID `json:"workspace_id"`
	KnowledgeBaseID uuid.UUID `json:"knowledge_base_id"`
	IndexVersionID  uuid.UUID `json:"index_version_id"`

	ConfigPath              string `json:"config_path"`
	InputDir                string `json:"input_dir"`
	Namespace               string `json:"namespace"`
	KnowledgeBaseExternalID string `json:"knowledge_base_id_external"`
	OutDir                  string `json:"out_dir"`
	Resume                  bool   `json:"resume"`
	QdrantCollection        string `json:"qdrant_collection"`
	QdrantNamespace         string `json:"qdrant_namespace"`
}

func BuildIngestKnowledgeJobPayload(job IngestionJob, kb KnowledgeBase, version KnowledgeIndexVersion, req CreateIngestionRunRequest, cfg config.Config) map[string]any {
	payload := IngestKnowledgeJobPayload{
		IngestionJobID:          job.ID,
		WorkspaceID:             job.WorkspaceID,
		KnowledgeBaseID:         job.KnowledgeBaseID,
		ConfigPath:              cfg.Python.ConfigPath,
		Namespace:               defaultString(req.Namespace, version.QdrantNamespace),
		KnowledgeBaseExternalID: kb.ID.String(),
		OutDir:                  job.OutDir,
		Resume:                  req.Resume,
		QdrantCollection:        version.QdrantCollection,
		QdrantNamespace:         version.QdrantNamespace,
	}
	if job.IndexVersionID != nil {
		payload.IndexVersionID = *job.IndexVersionID
	} else {
		payload.IndexVersionID = version.ID
	}
	data, _ := json.Marshal(payload)
	var out map[string]any
	_ = json.Unmarshal(data, &out)
	return out
}

func ParseIngestKnowledgeJobPayload(payload map[string]any) (IngestKnowledgeJobPayload, error) {
	var parsed IngestKnowledgeJobPayload
	data, err := json.Marshal(payload)
	if err != nil {
		return parsed, fmt.Errorf("encode ingest knowledge payload: %w", err)
	}
	if err := json.Unmarshal(data, &parsed); err != nil {
		return parsed, fmt.Errorf("decode ingest knowledge payload: %w", err)
	}
	if parsed.IngestionJobID == uuid.Nil {
		return parsed, fmt.Errorf("ingestion_job_id is required")
	}
	if parsed.WorkspaceID == uuid.Nil {
		return parsed, fmt.Errorf("workspace_id is required")
	}
	if parsed.KnowledgeBaseID == uuid.Nil {
		return parsed, fmt.Errorf("knowledge_base_id is required")
	}
	if parsed.IndexVersionID == uuid.Nil {
		return parsed, fmt.Errorf("index_version_id is required")
	}
	if strings.TrimSpace(parsed.OutDir) == "" {
		return parsed, fmt.Errorf("out_dir is required")
	}
	return parsed, nil
}
