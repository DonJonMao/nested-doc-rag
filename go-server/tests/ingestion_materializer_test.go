package tests

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestIngestionMaterializerSuccess(t *testing.T) {
	workspaceID, kbID, docID, fileID := uuid.New(), uuid.New(), uuid.New(), uuid.New()
	bases := newFakeKnowledgeBaseRepo()
	docs := newFakeKnowledgeDocumentRepo()
	files := newFakeFileRepo()
	storage := newFakeObjectStorage()
	require.NoError(t, bases.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	require.NoError(t, docs.Create(context.Background(), knowledgepkg.KnowledgeDocument{ID: docID, KnowledgeBaseID: kbID, WorkspaceID: workspaceID, FileID: fileID, Filename: "能力清单.xlsx", DocumentRole: knowledgepkg.DocumentRoleKnowledgeBase, Namespace: "xixian_4", Status: knowledgepkg.KnowledgeDocumentStatusUploaded}))
	files.files[fileID] = filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "能力清单.xlsx", ObjectKey: "docs/doc.xlsx", FileCategory: filepkg.FileCategoryKnowledgeDocument, Status: filepkg.FileStatusActive}
	storage.objects["docs/doc.xlsx"] = []byte("knowledge")
	materializer := knowledgepkg.NewIngestionMaterializer(bases, docs, files, storage, zap.NewNop())

	inputDir, count, cleanup, err := materializer.MaterializeDocuments(context.Background(), workspaceID, kbID, t.TempDir())

	require.NoError(t, err)
	defer cleanup()
	require.Equal(t, 1, count)
	path := filepath.Join(inputDir, "xixian_4", "能力清单.xlsx")
	data, err := os.ReadFile(path)
	require.NoError(t, err)
	require.Equal(t, []byte("knowledge"), data)
}

func TestIngestionMaterializerNoDocuments(t *testing.T) {
	workspaceID, kbID := uuid.New(), uuid.New()
	bases := newFakeKnowledgeBaseRepo()
	require.NoError(t, bases.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	materializer := knowledgepkg.NewIngestionMaterializer(bases, newFakeKnowledgeDocumentRepo(), newFakeFileRepo(), newFakeObjectStorage(), zap.NewNop())

	_, _, _, err := materializer.MaterializeDocuments(context.Background(), workspaceID, kbID, t.TempDir())

	require.Error(t, err)
}

func TestIngestionMaterializerWorkspaceMismatch(t *testing.T) {
	bases := newFakeKnowledgeBaseRepo()
	kbID := uuid.New()
	require.NoError(t, bases.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: uuid.New(), Name: "kb"}))
	materializer := knowledgepkg.NewIngestionMaterializer(bases, newFakeKnowledgeDocumentRepo(), newFakeFileRepo(), newFakeObjectStorage(), zap.NewNop())

	_, _, _, err := materializer.MaterializeDocuments(context.Background(), uuid.New(), kbID, t.TempDir())

	require.Error(t, err)
}

func TestIngestionMaterializerStorageMissing(t *testing.T) {
	workspaceID, kbID, fileID := uuid.New(), uuid.New(), uuid.New()
	bases := newFakeKnowledgeBaseRepo()
	docs := newFakeKnowledgeDocumentRepo()
	files := newFakeFileRepo()
	require.NoError(t, bases.Create(context.Background(), knowledgepkg.KnowledgeBase{ID: kbID, WorkspaceID: workspaceID, Name: "kb"}))
	require.NoError(t, docs.Create(context.Background(), knowledgepkg.KnowledgeDocument{ID: uuid.New(), KnowledgeBaseID: kbID, WorkspaceID: workspaceID, FileID: fileID, Filename: "doc.xlsx", DocumentRole: knowledgepkg.DocumentRoleKnowledgeBase, Namespace: "ns", Status: knowledgepkg.KnowledgeDocumentStatusUploaded}))
	files.files[fileID] = filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "doc.xlsx", ObjectKey: "missing", FileCategory: filepkg.FileCategoryKnowledgeDocument, Status: filepkg.FileStatusActive}
	materializer := knowledgepkg.NewIngestionMaterializer(bases, docs, files, newFakeObjectStorage(), zap.NewNop())

	_, _, _, err := materializer.MaterializeDocuments(context.Background(), workspaceID, kbID, t.TempDir())

	require.Error(t, err)
}
