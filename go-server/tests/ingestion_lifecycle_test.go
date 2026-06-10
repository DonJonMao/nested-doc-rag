package tests

import (
	"context"
	"errors"
	"testing"

	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestIngestionLifecycleRunningAndSucceeded(t *testing.T) {
	bases, docs, versions, ingestions, ingestionID, versionID, kbID := newIngestionLifecycleFixture(t)
	lifecycle := knowledgepkg.NewIngestionLifecycleAdapter(ingestions, versions, bases, docs, zap.NewNop())

	require.NoError(t, lifecycle.MarkIngestionRunning(context.Background(), ingestionID, uuid.New()))
	running, err := ingestions.GetByID(context.Background(), ingestionID)
	require.NoError(t, err)
	require.Equal(t, knowledgepkg.IngestionJobStatusRunning, running.Status)
	for _, doc := range docs.docs {
		require.Equal(t, knowledgepkg.KnowledgeDocumentStatusIndexing, doc.Status)
	}

	result := &pythonpkg.IngestionResult{IngestionID: ingestionID, OutDir: "/tmp/out", ManifestPath: "/tmp/out/run_manifest.json"}
	require.NoError(t, lifecycle.MarkIngestionSucceeded(context.Background(), ingestionID, result))

	ingestion, err := ingestions.GetByID(context.Background(), ingestionID)
	require.NoError(t, err)
	require.Equal(t, knowledgepkg.IngestionJobStatusSucceeded, ingestion.Status)
	require.Equal(t, 100, ingestion.Progress)
	version, err := versions.GetByID(context.Background(), versionID)
	require.NoError(t, err)
	require.Equal(t, knowledgepkg.IndexVersionStatusReady, version.Status)
	require.Equal(t, "/tmp/out", version.ArtifactDir)
	require.Equal(t, "/tmp/out/run_manifest.json", version.ManifestPath)
	kb, err := bases.GetByID(context.Background(), kbID)
	require.NoError(t, err)
	require.NotNil(t, kb.CurrentIndexVersionID)
	require.Equal(t, versionID, *kb.CurrentIndexVersionID)
	for _, doc := range docs.docs {
		require.Equal(t, knowledgepkg.KnowledgeDocumentStatusIndexed, doc.Status)
	}
}

func TestIngestionLifecycleFailedAndCanceled(t *testing.T) {
	bases, docs, versions, ingestions, failedID, failedVersionID, _ := newIngestionLifecycleFixture(t)
	_, _, _, _, canceledID, canceledVersionID, _ := newIngestionLifecycleFixtureWithRepos(t, bases, docs, versions, ingestions)
	lifecycle := knowledgepkg.NewIngestionLifecycleAdapter(ingestions, versions, bases, docs, zap.NewNop())

	require.NoError(t, lifecycle.MarkIngestionFailed(context.Background(), failedID, errors.New("python failed")))
	require.NoError(t, lifecycle.MarkIngestionCanceled(context.Background(), canceledID))

	failed, err := ingestions.GetByID(context.Background(), failedID)
	require.NoError(t, err)
	require.Equal(t, knowledgepkg.IngestionJobStatusFailed, failed.Status)
	require.Contains(t, failed.ErrorMessage, "python failed")
	failedVersion, err := versions.GetByID(context.Background(), failedVersionID)
	require.NoError(t, err)
	require.Equal(t, knowledgepkg.IndexVersionStatusFailed, failedVersion.Status)

	canceled, err := ingestions.GetByID(context.Background(), canceledID)
	require.NoError(t, err)
	require.Equal(t, knowledgepkg.IngestionJobStatusCanceled, canceled.Status)
	canceledVersion, err := versions.GetByID(context.Background(), canceledVersionID)
	require.NoError(t, err)
	require.Equal(t, knowledgepkg.IndexVersionStatusFailed, canceledVersion.Status)
	require.Equal(t, "canceled", canceledVersion.ErrorMessage)
}

func newIngestionLifecycleFixture(t *testing.T) (*fakeKnowledgeBaseRepo, *fakeKnowledgeDocumentRepo, *fakeKnowledgeIndexVersionRepo, *fakeIngestionJobRepo, uuid.UUID, uuid.UUID, uuid.UUID) {
	t.Helper()
	return newIngestionLifecycleFixtureWithRepos(t, newFakeKnowledgeBaseRepo(), newFakeKnowledgeDocumentRepo(), newFakeKnowledgeIndexVersionRepo(), newFakeIngestionJobRepo())
}

func newIngestionLifecycleFixtureWithRepos(t *testing.T, bases *fakeKnowledgeBaseRepo, docs *fakeKnowledgeDocumentRepo, versions *fakeKnowledgeIndexVersionRepo, ingestions *fakeIngestionJobRepo) (*fakeKnowledgeBaseRepo, *fakeKnowledgeDocumentRepo, *fakeKnowledgeIndexVersionRepo, *fakeIngestionJobRepo, uuid.UUID, uuid.UUID, uuid.UUID) {
	t.Helper()
	workspaceID := uuid.New()
	kbID := uuid.New()
	versionID := uuid.New()
	ingestionID := uuid.New()
	require.NoError(t, bases.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	require.NoError(t, versions.Create(context.Background(), knowledgepkg.KnowledgeIndexVersion{ID: versionID, KnowledgeBaseID: kbID, WorkspaceID: workspaceID, Status: knowledgepkg.IndexVersionStatusBuilding}))
	require.NoError(t, docs.Create(context.Background(), knowledgepkg.KnowledgeDocument{ID: uuid.New(), KnowledgeBaseID: kbID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "doc.xlsx", DocumentRole: knowledgepkg.DocumentRoleKnowledgeBase, Namespace: "ns", Status: knowledgepkg.KnowledgeDocumentStatusUploaded}))
	require.NoError(t, ingestions.Create(context.Background(), knowledgepkg.IngestionJob{ID: ingestionID, WorkspaceID: workspaceID, KnowledgeBaseID: kbID, IndexVersionID: &versionID, Status: knowledgepkg.IngestionJobStatusQueued, DocumentCount: 1}))
	return bases, docs, versions, ingestions, ingestionID, versionID, kbID
}
