package tests

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFileServiceUploadSuccessWritesStorageAndRepo(t *testing.T) {
	service, repo, storage, audits, _, actor, workspaceID := newFileServiceFixture(t)

	result, err := service.Upload(context.Background(), uploadReq(workspaceID), actor)

	require.NoError(t, err)
	require.NotEqual(t, uuid.Nil, result.ID)
	require.Equal(t, filepkg.FileCategoryFormTemplate, result.FileCategory)
	require.Len(t, storage.putLog, 1)
	require.Len(t, repo.files, 1)
	require.Len(t, audits.logs, 1)
	require.Equal(t, "file.uploaded", audits.logs[0].Action)
}

func TestFileServiceUploadStorageFailureDoesNotWriteRepo(t *testing.T) {
	service, repo, storage, _, _, actor, workspaceID := newFileServiceFixture(t)
	storage.putErr = errors.New("storage down")

	_, err := service.Upload(context.Background(), uploadReq(workspaceID), actor)

	require.Error(t, err)
	require.Zero(t, repo.createCount)
}

func TestFileServiceUploadRepoFailureDeletesUploadedObject(t *testing.T) {
	service, repo, storage, _, _, actor, workspaceID := newFileServiceFixture(t)
	repo.createErr = httpx.NewAppError(httpx.CodeConflict, "conflict", http.StatusConflict, nil, nil)

	_, err := service.Upload(context.Background(), uploadReq(workspaceID), actor)

	require.Error(t, err)
	require.Len(t, storage.deleteLog, 1)
}

func TestFileServiceDownloadChecksWorkspaceReadPermission(t *testing.T) {
	service, repo, storage, _, authorizer, actor, workspaceID := newFileServiceFixture(t)
	record := seedFile(repo, storage, workspaceID, actor.UserID)
	authorizer.readErr = httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)

	_, err := service.Download(context.Background(), record.ID, actor)

	requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
	require.Equal(t, 1, authorizer.reads)
}

func TestFileServiceDeleteChecksWorkspaceWritePermission(t *testing.T) {
	service, repo, storage, _, authorizer, actor, workspaceID := newFileServiceFixture(t)
	record := seedFile(repo, storage, workspaceID, actor.UserID)
	authorizer.writeErr = httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)

	err := service.Delete(context.Background(), record.ID, actor)

	requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
	require.Empty(t, repo.softDeleted)
}

func TestFileServiceAuditOnDownloadAndDelete(t *testing.T) {
	service, repo, storage, audits, _, actor, workspaceID := newFileServiceFixture(t)
	record := seedFile(repo, storage, workspaceID, actor.UserID)

	download, err := service.Download(context.Background(), record.ID, actor)
	require.NoError(t, err)
	_, _ = io.ReadAll(download.Reader)
	_ = download.Reader.Close()
	require.NoError(t, service.Delete(context.Background(), record.ID, actor))

	require.Len(t, audits.logs, 2)
	require.Equal(t, "file.downloaded", audits.logs[0].Action)
	require.Equal(t, "file.deleted", audits.logs[1].Action)
}

func newFileServiceFixture(t *testing.T) (*filepkg.Service, *fakeFileRepo, *fakeObjectStorage, *fakeAuditRepo, *fakeAuthorizer, auth.Principal, uuid.UUID) {
	t.Helper()
	repo := newFakeFileRepo()
	objectStorage := newFakeObjectStorage()
	authorizer := &fakeAuthorizer{}
	audits := &fakeAuditRepo{}
	validator := filepkg.NewValidator(1024*1024, []string{".png", ".xlsx", ".docx", ".jpg", ".jpeg"}, []string{"image/png", "image/jpeg", "application/octet-stream"})
	service := filepkg.NewService(repo, objectStorage, authorizer, audit.NewService(audits, zap.NewNop()), validator, t.TempDir(), true)
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	return service, repo, objectStorage, audits, authorizer, actor, uuid.New()
}

func uploadReq(workspaceID uuid.UUID) filepkg.UploadFileRequest {
	data := append([]byte{0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'}, bytes.Repeat([]byte{0}, 32)...)
	return filepkg.UploadFileRequest{
		WorkspaceID:      workspaceID,
		OriginalFilename: "工勘截图.png",
		Size:             int64(len(data)),
		MIMEType:         "image/png",
		Category:         filepkg.FileCategoryFormTemplate,
		Reader:           bytes.NewReader(data),
	}
}

func seedFile(repo *fakeFileRepo, objectStorage *fakeObjectStorage, workspaceID uuid.UUID, userID uuid.UUID) filepkg.File {
	record := filepkg.File{
		ID:               uuid.New(),
		WorkspaceID:      workspaceID,
		Filename:         "test.png",
		OriginalFilename: "test.png",
		ObjectKey:        filepkg.BuildFileObjectKey(workspaceID, uuid.New(), filepkg.FileCategoryMisc, "test.png"),
		FileSize:         12,
		MIMEType:         "image/png",
		SHA256:           "sha",
		FileCategory:     filepkg.FileCategoryMisc,
		Status:           filepkg.FileStatusActive,
		CreatedBy:        userID,
	}
	repo.files[record.ID] = record
	objectStorage.objects[record.ObjectKey] = []byte("file content")
	return record
}
