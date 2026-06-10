package tests

import (
	"context"
	"errors"
	"net/http"
	"sync"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/google/uuid"
)

type fakeFormFileRepo struct {
	mu    sync.Mutex
	forms map[uuid.UUID]formpkg.FormFile
	err   error
}

func newFakeFormFileRepo() *fakeFormFileRepo {
	return &fakeFormFileRepo{forms: make(map[uuid.UUID]formpkg.FormFile)}
}

func (f *fakeFormFileRepo) Create(ctx context.Context, form formpkg.FormFile) error {
	if f.err != nil {
		return f.err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.forms[form.ID] = form
	return nil
}

func (f *fakeFormFileRepo) GetByID(ctx context.Context, id uuid.UUID) (*formpkg.FormFile, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	item, ok := f.forms[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "form file not found", http.StatusNotFound, nil, nil)
	}
	return &item, nil
}

func (f *fakeFormFileRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]formpkg.FormFile, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []formpkg.FormFile
	for _, item := range f.forms {
		if item.WorkspaceID == workspaceID {
			out = append(out, item)
		}
	}
	return out, nil
}

type fakeFillRunRepo struct {
	mu   sync.Mutex
	runs map[uuid.UUID]formpkg.FillRun
}

func newFakeFillRunRepo() *fakeFillRunRepo {
	return &fakeFillRunRepo{runs: make(map[uuid.UUID]formpkg.FillRun)}
}

func (f *fakeFillRunRepo) Create(ctx context.Context, run formpkg.FillRun) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.runs[run.ID] = run
	return nil
}

func (f *fakeFillRunRepo) GetByID(ctx context.Context, id uuid.UUID) (*formpkg.FillRun, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	run, ok := f.runs[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "fill run not found", http.StatusNotFound, nil, nil)
	}
	return &run, nil
}

func (f *fakeFillRunRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int) ([]formpkg.FillRun, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []formpkg.FillRun
	for _, run := range f.runs {
		if run.WorkspaceID == workspaceID && (status == "" || run.Status == status) {
			out = append(out, run)
		}
	}
	return out, nil
}

func (f *fakeFillRunRepo) AttachJob(ctx context.Context, runID uuid.UUID, jobID uuid.UUID, queuedAt time.Time) error {
	return f.update(runID, func(run *formpkg.FillRun) {
		run.JobID = &jobID
		run.Status = formpkg.FillRunStatusQueued
		run.QueuedAt = &queuedAt
	})
}

func (f *fakeFillRunRepo) MarkRunning(ctx context.Context, runID uuid.UUID, startedAt time.Time) error {
	return f.update(runID, func(run *formpkg.FillRun) {
		run.Status = formpkg.FillRunStatusRunning
		run.StartedAt = &startedAt
	})
}

func (f *fakeFillRunRepo) MarkSucceeded(ctx context.Context, runID uuid.UUID, finishedAt time.Time, update formpkg.FillRunCompletionUpdate) error {
	return f.complete(runID, formpkg.FillRunStatusSucceeded, finishedAt, update, "")
}

func (f *fakeFillRunRepo) MarkCompletedWithFailures(ctx context.Context, runID uuid.UUID, finishedAt time.Time, update formpkg.FillRunCompletionUpdate, errMsg string) error {
	return f.complete(runID, formpkg.FillRunStatusCompletedWithFailures, finishedAt, update, errMsg)
}

func (f *fakeFillRunRepo) MarkFailed(ctx context.Context, runID uuid.UUID, finishedAt time.Time, errMsg string) error {
	return f.update(runID, func(run *formpkg.FillRun) {
		run.Status = formpkg.FillRunStatusFailed
		run.FinishedAt = &finishedAt
		run.ErrorMessage = errMsg
	})
}

func (f *fakeFillRunRepo) RequestCancel(ctx context.Context, runID uuid.UUID, t time.Time) error {
	return f.update(runID, func(run *formpkg.FillRun) { run.Status = formpkg.FillRunStatusCancelRequested })
}

func (f *fakeFillRunRepo) MarkCanceled(ctx context.Context, runID uuid.UUID, finishedAt time.Time) error {
	return f.update(runID, func(run *formpkg.FillRun) {
		run.Status = formpkg.FillRunStatusCanceled
		run.FinishedAt = &finishedAt
	})
}

func (f *fakeFillRunRepo) UpdateProgress(ctx context.Context, runID uuid.UUID, progressDone int, progressTotal int) error {
	return f.update(runID, func(run *formpkg.FillRun) {
		run.ProgressDone = progressDone
		run.ProgressTotal = progressTotal
	})
}

func (f *fakeFillRunRepo) complete(runID uuid.UUID, status string, finishedAt time.Time, update formpkg.FillRunCompletionUpdate, errMsg string) error {
	return f.update(runID, func(run *formpkg.FillRun) {
		run.Status = status
		run.FinishedAt = &finishedAt
		run.RunManifestPath = update.RunManifestPath
		run.SummaryPath = update.SummaryPath
		run.FilledFormArtifactID = update.FilledFormArtifactID
		run.ProgressTotal = update.ProgressTotal
		run.ProgressDone = update.ProgressDone
		run.AnsweredCount = update.AnsweredCount
		run.PartialClueCount = update.PartialClueCount
		run.NotFoundCount = update.NotFoundCount
		run.ConflictUnresolvedCount = update.ConflictUnresolvedCount
		run.ReviewRequiredCount = update.ReviewRequiredCount
		run.WritebackAllowedCount = update.WritebackAllowedCount
		run.FailedCount = update.FailedCount
		run.ErrorMessage = errMsg
	})
}

func (f *fakeFillRunRepo) update(runID uuid.UUID, fn func(*formpkg.FillRun)) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	run, ok := f.runs[runID]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "fill run not found", http.StatusNotFound, nil, nil)
	}
	fn(&run)
	run.UpdatedAt = time.Now().UTC()
	f.runs[runID] = run
	return nil
}

type fakeFileUploader struct {
	uploaded []filepkg.UploadFileRequest
	got      []uuid.UUID
	file     *filepkg.File
	getFile  *filepkg.File
	err      error
}

func (f *fakeFileUploader) Upload(ctx context.Context, req filepkg.UploadFileRequest, actor auth.Principal) (*filepkg.File, error) {
	f.uploaded = append(f.uploaded, req)
	if f.err != nil {
		return nil, f.err
	}
	if f.file != nil {
		return f.file, nil
	}
	return &filepkg.File{ID: uuid.New(), WorkspaceID: req.WorkspaceID, Filename: req.OriginalFilename, FileCategory: req.Category, Status: filepkg.FileStatusActive}, nil
}

func (f *fakeFileUploader) Get(ctx context.Context, fileID uuid.UUID, actor auth.Principal) (*filepkg.File, error) {
	f.got = append(f.got, fileID)
	if f.err != nil {
		return nil, f.err
	}
	if f.getFile != nil {
		return f.getFile, nil
	}
	return nil, errors.New("missing file")
}

type fakeJobUseCase struct {
	created  []jobs.CreateJobRequest
	canceled []uuid.UUID
	job      *jobs.Job
	cancel   *jobs.Job
	err      error
}

func (f *fakeJobUseCase) CreateJob(ctx context.Context, req jobs.CreateJobRequest, actor auth.Principal) (*jobs.Job, error) {
	f.created = append(f.created, req)
	if f.err != nil {
		return nil, f.err
	}
	if f.job != nil {
		return f.job, nil
	}
	return &jobs.Job{ID: uuid.New(), WorkspaceID: req.WorkspaceID, JobType: req.JobType, ResourceType: req.ResourceType, ResourceID: req.ResourceID, Status: jobs.JobStatusQueued}, nil
}

func (f *fakeJobUseCase) CancelJob(ctx context.Context, jobID uuid.UUID, actor auth.Principal) (*jobs.Job, error) {
	f.canceled = append(f.canceled, jobID)
	if f.err != nil {
		return nil, f.err
	}
	if f.cancel != nil {
		return f.cancel, nil
	}
	return &jobs.Job{ID: jobID, Status: jobs.JobStatusCancelRequested}, nil
}

type fakeFillArtifactService struct {
	artifacts     []artifact.RunArtifact
	download      *artifact.DownloadResult
	err           error
	listCalls     []uuid.UUID
	downloadCalls []uuid.UUID
}

func (f *fakeFillArtifactService) ListRunArtifacts(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, actor auth.Principal) ([]artifact.RunArtifact, error) {
	f.listCalls = append(f.listCalls, runID)
	if f.err != nil {
		return nil, f.err
	}
	return f.artifacts, nil
}

func (f *fakeFillArtifactService) DownloadArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	f.downloadCalls = append(f.downloadCalls, artifactID)
	if f.err != nil {
		return nil, f.err
	}
	return f.download, nil
}
