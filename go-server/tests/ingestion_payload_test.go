package tests

import (
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestBuildIngestKnowledgeJobPayload(t *testing.T) {
	cfg := *config.Default()
	cfg.Python.ConfigPath = "config/local.yaml"
	kb := knowledgepkg.KnowledgeBase{ID: uuid.New(), WorkspaceID: uuid.New(), QdrantCollection: "collection"}
	versionID := uuid.New()
	job := knowledgepkg.IngestionJob{ID: uuid.New(), WorkspaceID: kb.WorkspaceID, KnowledgeBaseID: kb.ID, IndexVersionID: &versionID, OutDir: "out"}
	version := knowledgepkg.KnowledgeIndexVersion{ID: versionID, KnowledgeBaseID: kb.ID, WorkspaceID: kb.WorkspaceID, QdrantCollection: "collection", QdrantNamespace: "xixian_4"}

	payload := knowledgepkg.BuildIngestKnowledgeJobPayload(job, kb, version, knowledgepkg.CreateIngestionRunRequest{Namespace: "xixian_4", Resume: true}, cfg)

	require.Equal(t, job.ID.String(), payload["ingestion_job_id"])
	require.Equal(t, kb.WorkspaceID.String(), payload["workspace_id"])
	require.Equal(t, kb.ID.String(), payload["knowledge_base_id"])
	require.Equal(t, versionID.String(), payload["index_version_id"])
	require.Equal(t, "config/local.yaml", payload["config_path"])
	require.Equal(t, "", payload["input_dir"])
	require.Equal(t, "xixian_4", payload["namespace"])
	require.Equal(t, kb.ID.String(), payload["knowledge_base_id_external"])
	require.Equal(t, "out", payload["out_dir"])
	require.Equal(t, true, payload["resume"])
	require.Equal(t, "collection", payload["qdrant_collection"])
	require.Equal(t, "xixian_4", payload["qdrant_namespace"])
}

func TestParseIngestKnowledgeJobPayload(t *testing.T) {
	payload := map[string]any{
		"ingestion_job_id":  uuid.NewString(),
		"workspace_id":      uuid.NewString(),
		"knowledge_base_id": uuid.NewString(),
		"index_version_id":  uuid.NewString(),
		"out_dir":           "out",
	}

	parsed, err := knowledgepkg.ParseIngestKnowledgeJobPayload(payload)

	require.NoError(t, err)
	require.NotEqual(t, uuid.Nil, parsed.IngestionJobID)
	require.NotEqual(t, uuid.Nil, parsed.KnowledgeBaseID)
}

func TestParseIngestKnowledgeJobPayloadMissingRequired(t *testing.T) {
	_, err := knowledgepkg.ParseIngestKnowledgeJobPayload(map[string]any{"workspace_id": uuid.NewString()})

	require.Error(t, err)
	require.Contains(t, err.Error(), "ingestion_job_id is required")
}
