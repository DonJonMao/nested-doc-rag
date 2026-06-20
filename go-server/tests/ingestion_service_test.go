package tests

import (
	"context"
	"net/http"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestIngestionServiceCreateRunSuccess(t *testing.T) {
	bases, docs, versions, ingestions, jobSvc, service, kbID, workspaceID := newIngestionServiceFixture(t, true)

	ingestion, err := service.CreateIngestionRun(context.Background(), knowledgepkg.CreateIngestionRunRequest{KnowledgeBaseID: kbID, Namespace: "xixian_4", QdrantNamespace: "xixian_4", Resume: true}, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})

	require.NoError(t, err)
	require.Equal(t, knowledgepkg.IngestionJobStatusQueued, ingestion.Status)
	require.NotNil(t, ingestion.JobID)
	require.Len(t, versions.versions, 1)
	require.Len(t, ingestions.ingestions, 1)
	require.Len(t, jobSvc.created, 1)
	require.Equal(t, jobs.JobTypeIngestKnowledge, jobSvc.created[0].JobType)
	require.Equal(t, jobs.ResourceTypeKnowledgeBase, jobSvc.created[0].ResourceType)
	require.Equal(t, ingestion.ID, jobSvc.created[0].ResourceID)
	require.Equal(t, ingestion.ID.String(), jobSvc.created[0].Payload["ingestion_job_id"])
	require.Equal(t, workspaceID.String(), jobSvc.created[0].Payload["workspace_id"])
	require.NotEmpty(t, jobSvc.created[0].Payload["index_version_id"])
	require.NotEmpty(t, ingestion.OutDir)
	require.NotEmpty(t, bases.bases[kbID].QdrantCollection)
	require.Len(t, docs.docs, 1)
}

func TestIngestionServiceRequiresActiveDocuments(t *testing.T) {
	_, docs, _, _, _, service, kbID, _ := newIngestionServiceFixture(t, true)
	for id, doc := range docs.docs {
		doc.Status = knowledgepkg.KnowledgeDocumentStatusDeleted
		docs.docs[id] = doc
	}

	_, err := service.CreateIngestionRun(context.Background(), knowledgepkg.CreateIngestionRunRequest{KnowledgeBaseID: kbID, Namespace: "ns"}, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})

	require.Error(t, err)
	require.Equal(t, httpx.CodeInvalidArgument, httpx.ErrorFrom(err).Code)
}

func TestIngestionServiceDisabledReturnsFeatureDisabled(t *testing.T) {
	_, _, _, _, jobSvc, service, kbID, _ := newIngestionServiceFixture(t, false)

	_, err := service.CreateIngestionRun(context.Background(), knowledgepkg.CreateIngestionRunRequest{KnowledgeBaseID: kbID, Namespace: "ns"}, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})

	require.Error(t, err)
	require.Equal(t, httpx.CodeFeatureDisabled, httpx.ErrorFrom(err).Code)
	require.Empty(t, jobSvc.created)
}

func TestIngestionServiceCancelCallsJobService(t *testing.T) {
	_, _, _, ingestions, jobSvc, service, _, workspaceID := newIngestionServiceFixture(t, true)
	ingestionID := uuid.New()
	jobID := uuid.New()
	require.NoError(t, ingestions.Create(context.Background(), knowledgepkg.IngestionJob{ID: ingestionID, WorkspaceID: workspaceID, KnowledgeBaseID: uuid.New(), JobID: &jobID, Status: knowledgepkg.IngestionJobStatusRunning}))
	jobSvc.cancel = &jobs.Job{ID: jobID, Status: jobs.JobStatusCancelRequested}

	ingestion, err := service.CancelIngestionJob(context.Background(), ingestionID, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})

	require.NoError(t, err)
	require.Equal(t, knowledgepkg.IngestionJobStatusCancelRequested, ingestion.Status)
	require.Equal(t, []uuid.UUID{jobID}, jobSvc.canceled)
}

func TestIngestionServiceCreateRequiresWorkspaceWrite(t *testing.T) {
	bases, docs, versions, ingestions, jobSvc, _, kbID, _ := newIngestionServiceFixture(t, true)
	service := knowledgepkg.NewIngestionService(bases, docs, versions, ingestions, jobSvc, &fakeAuthorizer{writeErr: httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)}, nil, zap.NewNop(), serviceConfig(true))

	_, err := service.CreateIngestionRun(context.Background(), knowledgepkg.CreateIngestionRunRequest{KnowledgeBaseID: kbID, Namespace: "ns"}, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}})

	require.Error(t, err)
	require.Empty(t, jobSvc.created)
}

func TestIngestionServiceCreateRequiresAdmin(t *testing.T) {
	_, _, _, _, jobSvc, service, kbID, _ := newIngestionServiceFixture(t, true)

	_, err := service.CreateIngestionRun(context.Background(), knowledgepkg.CreateIngestionRunRequest{KnowledgeBaseID: kbID, Namespace: "ns"}, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}})

	require.Error(t, err)
	require.Equal(t, httpx.CodeForbidden, httpx.ErrorFrom(err).Code)
	require.Empty(t, jobSvc.created)
}

func TestIngestionServiceReadRequiresAdmin(t *testing.T) {
	_, _, _, ingestions, _, service, kbID, workspaceID := newIngestionServiceFixture(t, true)
	ingestionID := uuid.New()
	require.NoError(t, ingestions.Create(context.Background(), knowledgepkg.IngestionJob{ID: ingestionID, WorkspaceID: workspaceID, KnowledgeBaseID: kbID, Status: knowledgepkg.IngestionJobStatusQueued}))

	_, err := service.GetIngestionJob(context.Background(), ingestionID, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}})
	require.Error(t, err)
	require.Equal(t, httpx.CodeForbidden, httpx.ErrorFrom(err).Code)

	_, err = service.ListIngestionJobs(context.Background(), kbID, "", 50, 0, auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}})
	require.Error(t, err)
	require.Equal(t, httpx.CodeForbidden, httpx.ErrorFrom(err).Code)
}

func newIngestionServiceFixture(t *testing.T, enabled bool) (*fakeKnowledgeBaseRepo, *fakeKnowledgeDocumentRepo, *fakeKnowledgeIndexVersionRepo, *fakeIngestionJobRepo, *fakeJobUseCase, *knowledgepkg.IngestionService, uuid.UUID, uuid.UUID) {
	t.Helper()
	bases := newFakeKnowledgeBaseRepo()
	docs := newFakeKnowledgeDocumentRepo()
	versions := newFakeKnowledgeIndexVersionRepo()
	ingestions := newFakeIngestionJobRepo()
	jobSvc := &fakeJobUseCase{}
	workspaceID := uuid.New()
	kbID := uuid.New()
	require.NoError(t, bases.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb", QdrantCollection: "collection"}))
	require.NoError(t, docs.Create(context.Background(), knowledgepkg.KnowledgeDocument{ID: uuid.New(), KnowledgeBaseID: kbID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "doc.xlsx", DocumentRole: knowledgepkg.DocumentRoleKnowledgeBase, Namespace: "ns", Status: knowledgepkg.KnowledgeDocumentStatusUploaded}))
	cfg := serviceConfig(enabled)
	service := knowledgepkg.NewIngestionService(bases, docs, versions, ingestions, jobSvc, &fakeAuthorizer{}, nil, zap.NewNop(), cfg)
	return bases, docs, versions, ingestions, jobSvc, service, kbID, workspaceID
}

func serviceConfig(enabled bool) config.Config {
	cfg := *config.Default()
	cfg.Python.ProjectDir = "/tmp/project"
	cfg.Python.ConfigPath = "config/local.yaml"
	cfg.Python.IngestCommandEnabled = enabled
	return cfg
}
