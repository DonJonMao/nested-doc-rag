package tests

import (
	"context"
	"net/http"
	"strings"
	"testing"

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
	files := &fakeFileUploader{file: &filepkg.File{ID: uuid.New(), WorkspaceID: workspaceID, Filename: "form.xlsx", FileCategory: filepkg.FileCategoryFormTemplate, Status: filepkg.FileStatusActive}}
	service := formpkg.NewFormFileService(repo, files, &fakeAuthorizer{}, nil, zap.NewNop())

	formFile, err := service.UploadForm(context.Background(), formpkg.UploadFormRequest{WorkspaceID: workspaceID, OriginalFilename: "form.xlsx", Size: 2, MIMEType: "application/octet-stream", Reader: strings.NewReader("xx")}, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Equal(t, workspaceID, formFile.WorkspaceID)
	require.Len(t, files.uploaded, 1)
	require.Equal(t, filepkg.FileCategoryFormTemplate, files.uploaded[0].Category)
}

func TestFormFileServiceUploadRequiresWorkspaceWrite(t *testing.T) {
	service := formpkg.NewFormFileService(newFakeFormFileRepo(), &fakeFileUploader{}, &fakeAuthorizer{writeErr: httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)}, nil, zap.NewNop())

	_, err := service.UploadForm(context.Background(), formpkg.UploadFormRequest{WorkspaceID: uuid.New(), Reader: strings.NewReader("x")}, auth.Principal{UserID: uuid.New()})

	require.Error(t, err)
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
