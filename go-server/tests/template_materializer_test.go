package tests

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestTemplateMaterializerSuccess(t *testing.T) {
	workspaceID := uuid.New()
	formID := uuid.New()
	fileID := uuid.New()
	formRepo := newFakeFormFileRepo()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: fileID, Filename: "template.xlsx"}))
	fileRepo := newFakeFileRepo()
	fileRepo.files[fileID] = filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "template.xlsx", ObjectKey: "forms/template.xlsx", FileCategory: filepkg.FileCategoryFormTemplate, Status: filepkg.FileStatusActive}
	storage := newFakeObjectStorage()
	storage.objects["forms/template.xlsx"] = []byte("template")
	materializer := formpkg.NewTemplateMaterializer(formRepo, fileRepo, storage, zap.NewNop())

	path, cleanup, err := materializer.MaterializeTemplate(context.Background(), workspaceID, formID, t.TempDir())

	require.NoError(t, err)
	defer cleanup()
	data, err := os.ReadFile(path)
	require.NoError(t, err)
	require.Equal(t, []byte("template"), data)
	require.Equal(t, "input", filepath.Base(filepath.Dir(path)))
}

func TestTemplateMaterializerWorkspaceMismatchRejected(t *testing.T) {
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: uuid.New(), FileID: uuid.New(), Filename: "template.xlsx"}))
	materializer := formpkg.NewTemplateMaterializer(formRepo, newFakeFileRepo(), newFakeObjectStorage(), zap.NewNop())

	_, _, err := materializer.MaterializeTemplate(context.Background(), uuid.New(), formID, t.TempDir())

	require.Error(t, err)
}

func TestTemplateMaterializerMissingObjectReturnsError(t *testing.T) {
	workspaceID := uuid.New()
	formID := uuid.New()
	fileID := uuid.New()
	formRepo := newFakeFormFileRepo()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: fileID, Filename: "template.xlsx"}))
	fileRepo := newFakeFileRepo()
	fileRepo.files[fileID] = filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "template.xlsx", ObjectKey: "missing", FileCategory: filepkg.FileCategoryFormTemplate, Status: filepkg.FileStatusActive}
	materializer := formpkg.NewTemplateMaterializer(formRepo, fileRepo, newFakeObjectStorage(), zap.NewNop())

	_, _, err := materializer.MaterializeTemplate(context.Background(), workspaceID, formID, t.TempDir())

	require.Error(t, err)
}
