package tests

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
	"github.com/google/uuid"
)

type fakeObjectStorage struct {
	mu         sync.Mutex
	objects    map[string][]byte
	putErr     error
	getErr     error
	presignURL string
	presignErr error
	deleteLog  []string
	putLog     []string
}

func newFakeObjectStorage() *fakeObjectStorage {
	return &fakeObjectStorage{objects: make(map[string][]byte)}
}

func (f *fakeObjectStorage) Put(ctx context.Context, key string, r io.Reader, size int64, contentType string) error {
	if f.putErr != nil {
		return f.putErr
	}
	data, err := io.ReadAll(r)
	if err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.objects[key] = data
	f.putLog = append(f.putLog, key)
	return nil
}

func (f *fakeObjectStorage) Get(ctx context.Context, key string) (io.ReadCloser, storage.ObjectInfo, error) {
	if f.getErr != nil {
		return nil, storage.ObjectInfo{}, f.getErr
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	data, ok := f.objects[key]
	if !ok {
		return nil, storage.ObjectInfo{}, errors.New("not found")
	}
	return io.NopCloser(bytes.NewReader(data)), storage.ObjectInfo{Key: key, Size: int64(len(data)), ContentType: "application/octet-stream"}, nil
}

func (f *fakeObjectStorage) Delete(ctx context.Context, key string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.objects, key)
	f.deleteLog = append(f.deleteLog, key)
	return nil
}

func (f *fakeObjectStorage) PresignGet(ctx context.Context, key string, ttl time.Duration) (string, error) {
	if f.presignErr != nil {
		return "", f.presignErr
	}
	if f.presignURL != "" {
		return f.presignURL, nil
	}
	return "", storage.ErrNotSupported
}

func (f *fakeObjectStorage) Health(ctx context.Context) error {
	return nil
}

type fakeAuthorizer struct {
	readErr  error
	writeErr error
	reads    int
	writes   int
}

func (f *fakeAuthorizer) CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	f.reads++
	return f.readErr
}

func (f *fakeAuthorizer) CanWriteWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	f.writes++
	return f.writeErr
}

type fakeFileRepo struct {
	mu          sync.Mutex
	files       map[uuid.UUID]filepkg.File
	createErr   error
	softDeleted []uuid.UUID
	createCount int
}

func newFakeFileRepo() *fakeFileRepo {
	return &fakeFileRepo{files: make(map[uuid.UUID]filepkg.File)}
}

func (f *fakeFileRepo) Create(ctx context.Context, file filepkg.File) error {
	if f.createErr != nil {
		return f.createErr
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.files[file.ID] = file
	f.createCount++
	return nil
}

func (f *fakeFileRepo) GetByID(ctx context.Context, id uuid.UUID) (*filepkg.File, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	file, ok := f.files[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "file not found", http.StatusNotFound, nil, nil)
	}
	return &file, nil
}

func (f *fakeFileRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, category string, limit int, offset int) ([]filepkg.File, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var files []filepkg.File
	for _, file := range f.files {
		if file.WorkspaceID == workspaceID && file.Status == filepkg.FileStatusActive && (category == "" || file.FileCategory == category) {
			files = append(files, file)
		}
	}
	return files, nil
}

func (f *fakeFileRepo) SoftDelete(ctx context.Context, id uuid.UUID, deletedAt time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	file, ok := f.files[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "file not found", http.StatusNotFound, nil, nil)
	}
	file.Status = filepkg.FileStatusDeleted
	file.DeletedAt = &deletedAt
	f.files[id] = file
	f.softDeleted = append(f.softDeleted, id)
	return nil
}

func (f *fakeFileRepo) ExistsByHash(ctx context.Context, workspaceID uuid.UUID, sha256 string) (bool, error) {
	return false, nil
}

type fakeArtifactRepo struct {
	mu        sync.Mutex
	artifacts map[uuid.UUID]artifact.RunArtifact
	createErr error
}

func newFakeArtifactRepo() *fakeArtifactRepo {
	return &fakeArtifactRepo{artifacts: make(map[uuid.UUID]artifact.RunArtifact)}
}

func (f *fakeArtifactRepo) Create(ctx context.Context, item artifact.RunArtifact) error {
	if f.createErr != nil {
		return f.createErr
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.artifacts[item.ID] = item
	return nil
}

func (f *fakeArtifactRepo) GetByID(ctx context.Context, id uuid.UUID) (*artifact.RunArtifact, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	item, ok := f.artifacts[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "artifact not found", http.StatusNotFound, nil, nil)
	}
	return &item, nil
}

func (f *fakeArtifactRepo) ListByRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID) ([]artifact.RunArtifact, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var items []artifact.RunArtifact
	for _, item := range f.artifacts {
		if item.WorkspaceID == workspaceID && item.RunID == runID {
			items = append(items, item)
		}
	}
	return items, nil
}

func (f *fakeArtifactRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]artifact.RunArtifact, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var items []artifact.RunArtifact
	for _, item := range f.artifacts {
		if item.WorkspaceID == workspaceID {
			items = append(items, item)
		}
	}
	return items, nil
}
