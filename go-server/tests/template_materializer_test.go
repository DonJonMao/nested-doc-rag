package tests

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"

	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
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

func TestTemplateMaterializerRejectsInactiveFile(t *testing.T) {
	workspaceID, formID, fileID := uuid.New(), uuid.New(), uuid.New()
	formRepo := newFakeFormFileRepo()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: fileID, Filename: "template.xlsx"}))
	fileRepo := newFakeFileRepo()
	fileRepo.files[fileID] = filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "template.xlsx", ObjectKey: "forms/template.xlsx", FileCategory: filepkg.FileCategoryFormTemplate, Status: filepkg.FileStatusDeleted}
	materializer := formpkg.NewTemplateMaterializer(formRepo, fileRepo, newFakeObjectStorage(), zap.NewNop())

	_, _, err := materializer.MaterializeTemplate(context.Background(), workspaceID, formID, t.TempDir())

	require.Error(t, err)
}

func TestTemplateMaterializerRejectsWrongFileCategory(t *testing.T) {
	workspaceID, formID, fileID := uuid.New(), uuid.New(), uuid.New()
	formRepo := newFakeFormFileRepo()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: fileID, Filename: "knowledge.pdf"}))
	fileRepo := newFakeFileRepo()
	fileRepo.files[fileID] = filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "knowledge.pdf", ObjectKey: "docs/knowledge.pdf", FileCategory: filepkg.FileCategoryKnowledgeDocument, Status: filepkg.FileStatusActive}
	materializer := formpkg.NewTemplateMaterializer(formRepo, fileRepo, newFakeObjectStorage(), zap.NewNop())

	_, _, err := materializer.MaterializeTemplate(context.Background(), workspaceID, formID, t.TempDir())

	require.Error(t, err)
}

func TestTemplateMaterializerStreamsLargeTemplateToDisk(t *testing.T) {
	workspaceID, formID, fileID := uuid.New(), uuid.New(), uuid.New()
	formRepo := newFakeFormFileRepo()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: fileID, Filename: "large.xlsx"}))
	fileRepo := newFakeFileRepo()
	fileRepo.files[fileID] = filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "large.xlsx", ObjectKey: "forms/large.xlsx", FileCategory: filepkg.FileCategoryFormTemplate, Status: filepkg.FileStatusActive}
	objectSize := int64(2 << 20)
	streaming := &streamingObjectStorage{size: objectSize, chunkSize: 8192}
	materializer := formpkg.NewTemplateMaterializer(formRepo, fileRepo, streaming, zap.NewNop())

	path, cleanup, err := materializer.MaterializeTemplate(context.Background(), workspaceID, formID, t.TempDir())

	require.NoError(t, err)
	defer cleanup()
	info, err := os.Stat(path)
	require.NoError(t, err)
	require.Equal(t, objectSize, info.Size())
	require.Greater(t, streaming.readCalls, 1)
}

type streamingObjectStorage struct {
	size      int64
	chunkSize int
	readCalls int
}

func (s *streamingObjectStorage) Put(ctx context.Context, key string, r io.Reader, size int64, contentType string) error {
	_, err := io.Copy(io.Discard, r)
	return err
}

func (s *streamingObjectStorage) Get(ctx context.Context, key string) (io.ReadCloser, storage.ObjectInfo, error) {
	return &streamingReadCloser{remaining: s.size, chunkSize: s.chunkSize, onRead: func() { s.readCalls++ }}, storage.ObjectInfo{Key: key, Size: s.size, ContentType: "application/octet-stream"}, nil
}

func (s *streamingObjectStorage) Delete(ctx context.Context, key string) error {
	return nil
}

func (s *streamingObjectStorage) PresignGet(ctx context.Context, key string, ttl time.Duration) (string, error) {
	return "", storage.ErrNotSupported
}

func (s *streamingObjectStorage) Health(ctx context.Context) error {
	return nil
}

type streamingReadCloser struct {
	remaining int64
	chunkSize int
	onRead    func()
}

func (r *streamingReadCloser) Read(p []byte) (int, error) {
	if r.remaining <= 0 {
		return 0, io.EOF
	}
	if len(p) > r.chunkSize {
		p = p[:r.chunkSize]
	}
	if int64(len(p)) > r.remaining {
		p = p[:int(r.remaining)]
	}
	for i := range p {
		p[i] = 'x'
	}
	r.remaining -= int64(len(p))
	if r.onRead != nil {
		r.onRead()
	}
	return len(p), nil
}

func (r *streamingReadCloser) Close() error {
	return nil
}
