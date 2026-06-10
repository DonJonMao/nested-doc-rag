package tests

import (
	"context"
	"net/http"
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFormFileServiceUploadFormSuccess(t *testing.T) {
	repo := newFakeFormFileRepo()
	workspaceID := uuid.New()
	audits := &fakeAuditRepo{}
	files := &fakeFileUploader{file: &filepkg.File{ID: uuid.New(), WorkspaceID: workspaceID, Filename: "form.xlsx", FileCategory: filepkg.FileCategoryFormTemplate, Status: filepkg.FileStatusActive}}
	service := formpkg.NewFormFileService(repo, files, &fakeAuthorizer{}, audit.NewService(audits, zap.NewNop()), zap.NewNop())

	formFile, err := service.UploadForm(context.Background(), formpkg.UploadFormRequest{WorkspaceID: workspaceID, OriginalFilename: "form.xlsx", Size: 2, MIMEType: "application/octet-stream", Reader: strings.NewReader("xx")}, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Equal(t, workspaceID, formFile.WorkspaceID)
	require.Len(t, files.uploaded, 1)
	require.Equal(t, filepkg.FileCategoryFormTemplate, files.uploaded[0].Category)
	require.Len(t, repo.forms, 1)
	require.Len(t, audits.logs, 1)
	require.Equal(t, "form.uploaded", audits.logs[0].Action)
}

func TestFormFileServiceUploadRequiresWorkspaceWrite(t *testing.T) {
	repo := newFakeFormFileRepo()
	files := &fakeFileUploader{}
	service := formpkg.NewFormFileService(repo, files, &fakeAuthorizer{writeErr: httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)}, nil, zap.NewNop())

	_, err := service.UploadForm(context.Background(), formpkg.UploadFormRequest{WorkspaceID: uuid.New(), Reader: strings.NewReader("x")}, auth.Principal{UserID: uuid.New()})

	require.Error(t, err)
	require.Empty(t, files.uploaded)
	require.Empty(t, repo.forms)
}

func TestFormFileServiceRegisterExistingFileAsFormSuccess(t *testing.T) {
	repo := newFakeFormFileRepo()
	workspaceID := uuid.New()
	fileID := uuid.New()
	audits := &fakeAuditRepo{}
	files := &fakeFileUploader{getFile: &filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "existing.xlsx", FileCategory: filepkg.FileCategoryFormTemplate, Status: filepkg.FileStatusActive}}
	service := formpkg.NewFormFileService(repo, files, &fakeAuthorizer{}, audit.NewService(audits, zap.NewNop()), zap.NewNop())

	formFile, err := service.RegisterExistingFileAsForm(context.Background(), workspaceID, fileID, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Equal(t, fileID, formFile.FileID)
	require.Equal(t, "existing.xlsx", formFile.Filename)
	require.Equal(t, []uuid.UUID{fileID}, files.got)
	require.Len(t, repo.forms, 1)
	require.Len(t, audits.logs, 1)
	require.Equal(t, "form.registered", audits.logs[0].Action)
}

func TestFormFileServiceRegisterExistingFileRejectsNonFormFile(t *testing.T) {
	workspaceID := uuid.New()
	fileID := uuid.New()
	files := &fakeFileUploader{getFile: &filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "doc.pdf", FileCategory: filepkg.FileCategoryKnowledgeDocument, Status: filepkg.FileStatusActive}}
	repo := newFakeFormFileRepo()
	service := formpkg.NewFormFileService(repo, files, &fakeAuthorizer{}, nil, zap.NewNop())

	_, err := service.RegisterExistingFileAsForm(context.Background(), workspaceID, fileID, auth.Principal{UserID: uuid.New()})

	require.Error(t, err)
	appErr := httpx.ErrorFrom(err)
	require.Equal(t, httpx.CodeInvalidArgument, appErr.Code)
	require.Empty(t, repo.forms)
}

func TestFormFileServiceGetListRequiresRead(t *testing.T) {
	repo := newFakeFormFileRepo()
	workspaceID := uuid.New()
	formID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx"}))
	authorizer := &fakeAuthorizer{}
	service := formpkg.NewFormFileService(repo, &fakeFileUploader{}, authorizer, nil, zap.NewNop())

	_, err := service.GetForm(context.Background(), formID, auth.Principal{UserID: uuid.New()})
	require.NoError(t, err)
	forms, err := service.ListForms(context.Background(), workspaceID, 50, 0, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Len(t, forms, 1)
	require.Equal(t, 2, authorizer.reads)
}
