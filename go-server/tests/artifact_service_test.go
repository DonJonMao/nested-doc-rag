package tests

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestArtifactServiceRegisterSuccess(t *testing.T) {
	service, repo, storage, audits, _, actor, workspaceID, runID := newArtifactServiceFixture()

	result, err := service.RegisterArtifact(context.Background(), artifact.RegisterArtifactRequest{
		WorkspaceID:  workspaceID,
		RunID:        runID,
		ArtifactType: artifact.TypeRunManifest,
		Filename:     "run_manifest.json",
		ContentType:  "application/json",
		Reader:       strings.NewReader(`{"ok":true}`),
	}, actor)

	require.NoError(t, err)
	require.NotEqual(t, uuid.Nil, result.ID)
	require.Len(t, repo.artifacts, 1)
	require.Len(t, storage.putLog, 1)
	require.Len(t, audits.logs, 1)
	require.Equal(t, "artifact.registered", audits.logs[0].Action)
}

func TestArtifactServiceListByRun(t *testing.T) {
	service, repo, _, _, _, actor, workspaceID, runID := newArtifactServiceFixture()
	item := seedArtifact(repo, nil, workspaceID, runID, actor.UserID)

	items, err := service.ListRunArtifacts(context.Background(), workspaceID, runID, actor)

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, item.ID, items[0].ID)
}

func TestArtifactServiceDownloadChecksWorkspaceRead(t *testing.T) {
	service, repo, storage, _, authorizer, actor, workspaceID, runID := newArtifactServiceFixture()
	item := seedArtifact(repo, storage, workspaceID, runID, actor.UserID)
	authorizer.readErr = httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)

	_, err := service.DownloadArtifact(context.Background(), item.ID, actor)

	requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
	require.Equal(t, 1, authorizer.reads)
}

func TestArtifactServiceDownloadChecksRunAccess(t *testing.T) {
	service, repo, storage, _, authorizer, actor, workspaceID, runID := newArtifactServiceFixture()
	item := seedArtifact(repo, storage, workspaceID, runID, actor.UserID)
	service.SetRunAccessAuthorizer(denyingRunArtifactAuthorizer{})

	_, err := service.DownloadArtifact(context.Background(), item.ID, actor)

	requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
	require.Equal(t, 0, authorizer.reads)
}

func TestArtifactServiceAuditOnDownload(t *testing.T) {
	service, repo, storage, audits, _, actor, workspaceID, runID := newArtifactServiceFixture()
	item := seedArtifact(repo, storage, workspaceID, runID, actor.UserID)

	download, err := service.DownloadArtifact(context.Background(), item.ID, actor)
	require.NoError(t, err)
	_, _ = io.ReadAll(download.Reader)
	_ = download.Reader.Close()

	require.Len(t, audits.logs, 1)
	require.Equal(t, "artifact.downloaded", audits.logs[0].Action)
}

func TestArtifactServicePresignDownloadWhenEnabled(t *testing.T) {
	repo := newFakeArtifactRepo()
	objectStorage := newFakeObjectStorage()
	objectStorage.presignURL = "https://storage.local/presigned"
	authorizer := &fakeAuthorizer{}
	audits := &fakeAuditRepo{}
	service := artifact.NewService(
		repo,
		objectStorage,
		authorizer,
		audit.NewService(audits, zap.NewNop()),
		artifact.ServiceOptions{DownloadMode: "presign", AllowPresignDownload: true, DefaultPresignTTL: time.Minute},
	)
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	workspaceID := uuid.New()
	item := seedArtifact(repo, objectStorage, workspaceID, uuid.New(), actor.UserID)

	download, err := service.DownloadArtifact(context.Background(), item.ID, actor)

	require.NoError(t, err)
	require.Equal(t, "https://storage.local/presigned", download.PresignedURL)
	require.Len(t, audits.logs, 1)
	require.Equal(t, "artifact.downloaded", audits.logs[0].Action)
}

func newArtifactServiceFixture() (*artifact.Service, *fakeArtifactRepo, *fakeObjectStorage, *fakeAuditRepo, *fakeAuthorizer, auth.Principal, uuid.UUID, uuid.UUID) {
	repo := newFakeArtifactRepo()
	objectStorage := newFakeObjectStorage()
	authorizer := &fakeAuthorizer{}
	audits := &fakeAuditRepo{}
	service := artifact.NewService(repo, objectStorage, authorizer, audit.NewService(audits, zap.NewNop()))
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	return service, repo, objectStorage, audits, authorizer, actor, uuid.New(), uuid.New()
}

func seedArtifact(repo *fakeArtifactRepo, objectStorage *fakeObjectStorage, workspaceID uuid.UUID, runID uuid.UUID, userID uuid.UUID) artifact.RunArtifact {
	item := artifact.RunArtifact{
		ID:           uuid.New(),
		WorkspaceID:  workspaceID,
		RunID:        runID,
		ArtifactType: artifact.TypeRunManifest,
		Filename:     "run_manifest.json",
		ObjectKey:    "workspaces/" + workspaceID.String() + "/runs/" + runID.String() + "/artifacts/a/run_manifest.json",
		ContentType:  "application/json",
		FileSize:     2,
		SHA256:       "sha",
		CreatedBy:    userID,
	}
	repo.artifacts[item.ID] = item
	if objectStorage != nil {
		objectStorage.objects[item.ObjectKey] = []byte("{}")
	}
	return item
}

type denyingRunArtifactAuthorizer struct{}

func (denyingRunArtifactAuthorizer) CanReadRunArtifact(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, actor auth.Principal) error {
	return httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
}
