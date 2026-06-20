package tests

import (
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
)

type fakeKnowledgeBaseRepo struct {
	mu    sync.Mutex
	bases map[uuid.UUID]knowledgepkg.KnowledgeBase
}

func newFakeKnowledgeBaseRepo() *fakeKnowledgeBaseRepo {
	return &fakeKnowledgeBaseRepo{bases: make(map[uuid.UUID]knowledgepkg.KnowledgeBase)}
}

func (f *fakeKnowledgeBaseRepo) Create(ctx context.Context, kb knowledgepkg.KnowledgeBase) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	for _, existing := range f.bases {
		if existing.WorkspaceID == kb.WorkspaceID && existing.Name == kb.Name {
			return httpx.NewAppError(httpx.CodeConflict, "knowledge base already exists", http.StatusConflict, nil, nil)
		}
	}
	f.bases[kb.ID] = kb
	return nil
}

func (f *fakeKnowledgeBaseRepo) GetByID(ctx context.Context, id uuid.UUID) (*knowledgepkg.KnowledgeBase, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	kb, ok := f.bases[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "knowledge base not found", http.StatusNotFound, nil, nil)
	}
	return &kb, nil
}

func (f *fakeKnowledgeBaseRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]knowledgepkg.KnowledgeBase, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []knowledgepkg.KnowledgeBase
	for _, kb := range f.bases {
		if kb.WorkspaceID == workspaceID {
			out = append(out, kb)
		}
	}
	return out, nil
}

func (f *fakeKnowledgeBaseRepo) ListOptionsByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]knowledgepkg.KnowledgeBase, error) {
	return f.ListByWorkspace(ctx, workspaceID, limit, offset)
}

func (f *fakeKnowledgeBaseRepo) UpdateCurrentIndexVersion(ctx context.Context, kbID uuid.UUID, versionID uuid.UUID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	kb, ok := f.bases[kbID]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge base not found", http.StatusNotFound, nil, nil)
	}
	kb.CurrentIndexVersionID = &versionID
	kb.UpdatedAt = time.Now().UTC()
	f.bases[kbID] = kb
	return nil
}

func (f *fakeKnowledgeBaseRepo) UpdateStatus(ctx context.Context, kbID uuid.UUID, status string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	kb, ok := f.bases[kbID]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge base not found", http.StatusNotFound, nil, nil)
	}
	kb.Status = status
	kb.UpdatedAt = time.Now().UTC()
	f.bases[kbID] = kb
	return nil
}

func (f *fakeKnowledgeBaseRepo) Update(ctx context.Context, kb knowledgepkg.KnowledgeBase) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if _, ok := f.bases[kb.ID]; !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge base not found", http.StatusNotFound, nil, nil)
	}
	f.bases[kb.ID] = kb
	return nil
}

func (f *fakeKnowledgeBaseRepo) Delete(ctx context.Context, id uuid.UUID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.bases, id)
	return nil
}

type fakeKnowledgeDocumentRepo struct {
	mu   sync.Mutex
	docs map[uuid.UUID]knowledgepkg.KnowledgeDocument
}

func newFakeKnowledgeDocumentRepo() *fakeKnowledgeDocumentRepo {
	return &fakeKnowledgeDocumentRepo{docs: make(map[uuid.UUID]knowledgepkg.KnowledgeDocument)}
}

func (f *fakeKnowledgeDocumentRepo) Create(ctx context.Context, doc knowledgepkg.KnowledgeDocument) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.docs[doc.ID] = doc
	return nil
}

func (f *fakeKnowledgeDocumentRepo) GetByID(ctx context.Context, id uuid.UUID) (*knowledgepkg.KnowledgeDocument, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	doc, ok := f.docs[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "knowledge document not found", http.StatusNotFound, nil, nil)
	}
	return &doc, nil
}

func (f *fakeKnowledgeDocumentRepo) ListByKnowledgeBase(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int) ([]knowledgepkg.KnowledgeDocument, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []knowledgepkg.KnowledgeDocument
	for _, doc := range f.docs {
		if doc.KnowledgeBaseID == kbID && (status == "" || doc.Status == status) {
			out = append(out, doc)
		}
	}
	return out, nil
}

func (f *fakeKnowledgeDocumentRepo) ListActiveByKnowledgeBase(ctx context.Context, kbID uuid.UUID) ([]knowledgepkg.KnowledgeDocument, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []knowledgepkg.KnowledgeDocument
	for _, doc := range f.docs {
		if doc.KnowledgeBaseID == kbID && doc.Status != knowledgepkg.KnowledgeDocumentStatusDeleted {
			out = append(out, doc)
		}
	}
	return out, nil
}

func (f *fakeKnowledgeDocumentRepo) MarkStatus(ctx context.Context, id uuid.UUID, status string, errMsg string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	doc, ok := f.docs[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge document not found", http.StatusNotFound, nil, nil)
	}
	doc.Status = status
	doc.UpdatedAt = time.Now().UTC()
	f.docs[id] = doc
	return nil
}

func (f *fakeKnowledgeDocumentRepo) SoftDelete(ctx context.Context, id uuid.UUID) error {
	return f.MarkStatus(ctx, id, knowledgepkg.KnowledgeDocumentStatusDeleted, "")
}

type fakeKnowledgeIndexVersionRepo struct {
	mu       sync.Mutex
	versions map[uuid.UUID]knowledgepkg.KnowledgeIndexVersion
}

func newFakeKnowledgeIndexVersionRepo() *fakeKnowledgeIndexVersionRepo {
	return &fakeKnowledgeIndexVersionRepo{versions: make(map[uuid.UUID]knowledgepkg.KnowledgeIndexVersion)}
}

func (f *fakeKnowledgeIndexVersionRepo) Create(ctx context.Context, version knowledgepkg.KnowledgeIndexVersion) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.versions[version.ID] = version
	return nil
}

func (f *fakeKnowledgeIndexVersionRepo) GetByID(ctx context.Context, id uuid.UUID) (*knowledgepkg.KnowledgeIndexVersion, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	version, ok := f.versions[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "knowledge index version not found", http.StatusNotFound, nil, nil)
	}
	return &version, nil
}

func (f *fakeKnowledgeIndexVersionRepo) ListByKnowledgeBase(ctx context.Context, kbID uuid.UUID, limit int, offset int) ([]knowledgepkg.KnowledgeIndexVersion, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []knowledgepkg.KnowledgeIndexVersion
	for _, version := range f.versions {
		if version.KnowledgeBaseID == kbID {
			out = append(out, version)
		}
	}
	return out, nil
}

func (f *fakeKnowledgeIndexVersionRepo) NextVersion(ctx context.Context, kbID uuid.UUID) (int, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	maxVersion := 0
	for _, version := range f.versions {
		if version.KnowledgeBaseID == kbID && version.Version > maxVersion {
			maxVersion = version.Version
		}
	}
	return maxVersion + 1, nil
}

func (f *fakeKnowledgeIndexVersionRepo) MarkReady(ctx context.Context, id uuid.UUID, artifactDir string, manifestPath string, documentCount int, chunkCount int, readyAt time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	version, ok := f.versions[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge index version not found", http.StatusNotFound, nil, nil)
	}
	version.Status = knowledgepkg.IndexVersionStatusReady
	version.ArtifactDir = artifactDir
	version.ManifestPath = manifestPath
	version.DocumentCount = documentCount
	version.ChunkCount = chunkCount
	version.ReadyAt = &readyAt
	version.ErrorMessage = ""
	f.versions[id] = version
	return nil
}

func (f *fakeKnowledgeIndexVersionRepo) MarkFailed(ctx context.Context, id uuid.UUID, errMsg string, failedAt time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	version, ok := f.versions[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge index version not found", http.StatusNotFound, nil, nil)
	}
	version.Status = knowledgepkg.IndexVersionStatusFailed
	version.ErrorMessage = errMsg
	version.FailedAt = &failedAt
	f.versions[id] = version
	return nil
}

func (f *fakeKnowledgeIndexVersionRepo) ArchiveOldVersions(ctx context.Context, kbID uuid.UUID, exceptID uuid.UUID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	for id, version := range f.versions {
		if version.KnowledgeBaseID == kbID && id != exceptID && version.Status == knowledgepkg.IndexVersionStatusReady {
			version.Status = knowledgepkg.IndexVersionStatusArchived
			f.versions[id] = version
		}
	}
	return nil
}

type fakeIngestionJobRepo struct {
	mu         sync.Mutex
	ingestions map[uuid.UUID]knowledgepkg.IngestionJob
}

func newFakeIngestionJobRepo() *fakeIngestionJobRepo {
	return &fakeIngestionJobRepo{ingestions: make(map[uuid.UUID]knowledgepkg.IngestionJob)}
}

func (f *fakeIngestionJobRepo) Create(ctx context.Context, job knowledgepkg.IngestionJob) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.ingestions[job.ID] = job
	return nil
}

func (f *fakeIngestionJobRepo) GetByID(ctx context.Context, id uuid.UUID) (*knowledgepkg.IngestionJob, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	job, ok := f.ingestions[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "ingestion job not found", http.StatusNotFound, nil, nil)
	}
	return &job, nil
}

func (f *fakeIngestionJobRepo) ListByKnowledgeBase(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int) ([]knowledgepkg.IngestionJob, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []knowledgepkg.IngestionJob
	for _, job := range f.ingestions {
		if job.KnowledgeBaseID == kbID && (status == "" || job.Status == status) {
			out = append(out, job)
		}
	}
	return out, nil
}

func (f *fakeIngestionJobRepo) AttachJob(ctx context.Context, ingestionJobID uuid.UUID, jobID uuid.UUID, queuedAt time.Time) error {
	return f.update(ingestionJobID, func(job *knowledgepkg.IngestionJob) {
		job.JobID = &jobID
		job.Status = knowledgepkg.IngestionJobStatusQueued
	})
}

func (f *fakeIngestionJobRepo) MarkRunning(ctx context.Context, id uuid.UUID, startedAt time.Time) error {
	return f.update(id, func(job *knowledgepkg.IngestionJob) {
		job.Status = knowledgepkg.IngestionJobStatusRunning
		job.StartedAt = &startedAt
	})
}

func (f *fakeIngestionJobRepo) MarkSucceeded(ctx context.Context, id uuid.UUID, finishedAt time.Time, progress int) error {
	return f.update(id, func(job *knowledgepkg.IngestionJob) {
		job.Status = knowledgepkg.IngestionJobStatusSucceeded
		job.Progress = progress
		job.FinishedAt = &finishedAt
		job.ErrorMessage = ""
	})
}

func (f *fakeIngestionJobRepo) MarkFailed(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error {
	return f.update(id, func(job *knowledgepkg.IngestionJob) {
		job.Status = knowledgepkg.IngestionJobStatusFailed
		job.FinishedAt = &finishedAt
		job.ErrorMessage = errMsg
	})
}

func (f *fakeIngestionJobRepo) RequestCancel(ctx context.Context, id uuid.UUID, t time.Time) error {
	return f.update(id, func(job *knowledgepkg.IngestionJob) { job.Status = knowledgepkg.IngestionJobStatusCancelRequested })
}

func (f *fakeIngestionJobRepo) MarkCanceled(ctx context.Context, id uuid.UUID, finishedAt time.Time) error {
	return f.update(id, func(job *knowledgepkg.IngestionJob) {
		job.Status = knowledgepkg.IngestionJobStatusCanceled
		job.FinishedAt = &finishedAt
	})
}

func (f *fakeIngestionJobRepo) UpdateProgress(ctx context.Context, id uuid.UUID, progress int) error {
	return f.update(id, func(job *knowledgepkg.IngestionJob) { job.Progress = progress })
}

func (f *fakeIngestionJobRepo) update(id uuid.UUID, fn func(*knowledgepkg.IngestionJob)) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	job, ok := f.ingestions[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "ingestion job not found", http.StatusNotFound, nil, nil)
	}
	fn(&job)
	job.UpdatedAt = time.Now().UTC()
	f.ingestions[id] = job
	return nil
}
