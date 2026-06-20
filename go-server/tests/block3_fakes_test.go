package tests

import (
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
)

type fakeJobRepo struct {
	mu   sync.Mutex
	jobs map[uuid.UUID]jobs.Job
}

func newFakeJobRepo() *fakeJobRepo {
	return &fakeJobRepo{jobs: make(map[uuid.UUID]jobs.Job)}
}

func (f *fakeJobRepo) add(job jobs.Job) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if job.ID == uuid.Nil {
		job.ID = uuid.New()
	}
	if job.Payload == nil {
		job.Payload = map[string]any{}
	}
	f.jobs[job.ID] = job
}

func (f *fakeJobRepo) Create(ctx context.Context, job jobs.Job) error {
	f.add(job)
	return nil
}

func (f *fakeJobRepo) GetByID(ctx context.Context, id uuid.UUID) (*jobs.Job, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	job, ok := f.jobs[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "job not found", http.StatusNotFound, nil, nil)
	}
	copy := job
	return &copy, nil
}

func (f *fakeJobRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int) ([]jobs.Job, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []jobs.Job
	for _, job := range f.jobs {
		if job.WorkspaceID == workspaceID && (status == "" || job.Status == status) {
			out = append(out, job)
		}
	}
	return out, nil
}

func (f *fakeJobRepo) ListInterrupted(ctx context.Context, staleBefore time.Time, limit int) ([]jobs.Job, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []jobs.Job
	for _, job := range f.jobs {
		if job.Status != jobs.JobStatusRunning && job.Status != jobs.JobStatusCancelRequested {
			continue
		}
		stamp := job.UpdatedAt
		if job.StartedAt != nil {
			stamp = *job.StartedAt
		}
		if job.HeartbeatAt != nil {
			stamp = *job.HeartbeatAt
		}
		if stamp.IsZero() {
			stamp = job.CreatedAt
		}
		if stamp.Before(staleBefore) {
			out = append(out, job)
		}
	}
	return out, nil
}

func (f *fakeJobRepo) UpdateStatus(ctx context.Context, id uuid.UUID, fromStatus string, toStatus string, fields jobs.UpdateStatusFields) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	job, ok := f.jobs[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "job not found", http.StatusNotFound, nil, nil)
	}
	if job.Status != fromStatus {
		return httpx.NewAppError(httpx.CodeConflict, "job status changed", http.StatusConflict, nil, nil)
	}
	applyJobStatusFields(&job, toStatus, fields)
	f.jobs[id] = job
	return nil
}

func (f *fakeJobRepo) MarkQueued(ctx context.Context, id uuid.UUID, queuedAt time.Time) error {
	return f.updateAny(id, []string{jobs.JobStatusCreated, jobs.JobStatusFailed, jobs.JobStatusCompletedWithFailures}, jobs.JobStatusQueued, jobs.UpdateStatusFields{QueuedAt: &queuedAt})
}

func (f *fakeJobRepo) MarkRunning(ctx context.Context, id uuid.UUID, startedAt time.Time) error {
	return f.UpdateStatus(ctx, id, jobs.JobStatusQueued, jobs.JobStatusRunning, jobs.UpdateStatusFields{StartedAt: &startedAt, HeartbeatAt: &startedAt})
}

func (f *fakeJobRepo) MarkHeartbeat(ctx context.Context, id uuid.UUID, heartbeatAt time.Time) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	job, ok := f.jobs[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "job not found", http.StatusNotFound, nil, nil)
	}
	if job.Status != jobs.JobStatusRunning && job.Status != jobs.JobStatusCancelRequested {
		return httpx.NewAppError(httpx.CodeConflict, "job is not running", http.StatusConflict, nil, nil)
	}
	job.HeartbeatAt = &heartbeatAt
	f.jobs[id] = job
	return nil
}

func (f *fakeJobRepo) MarkSucceeded(ctx context.Context, id uuid.UUID, finishedAt time.Time) error {
	return f.UpdateStatus(ctx, id, jobs.JobStatusRunning, jobs.JobStatusSucceeded, jobs.UpdateStatusFields{FinishedAt: &finishedAt})
}

func (f *fakeJobRepo) MarkCompletedWithFailures(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error {
	return f.UpdateStatus(ctx, id, jobs.JobStatusRunning, jobs.JobStatusCompletedWithFailures, jobs.UpdateStatusFields{FinishedAt: &finishedAt, ErrorMessage: errMsg})
}

func (f *fakeJobRepo) MarkFailed(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error {
	return f.updateAny(id, []string{jobs.JobStatusRunning, jobs.JobStatusCancelRequested}, jobs.JobStatusFailed, jobs.UpdateStatusFields{FinishedAt: &finishedAt, ErrorMessage: errMsg})
}

func (f *fakeJobRepo) MarkEnqueueFailed(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error {
	return f.updateAny(id, []string{jobs.JobStatusCreated, jobs.JobStatusQueued}, jobs.JobStatusFailed, jobs.UpdateStatusFields{FinishedAt: &finishedAt, ErrorMessage: errMsg})
}

func (f *fakeJobRepo) RequestCancel(ctx context.Context, id uuid.UUID, t time.Time) error {
	return f.UpdateStatus(ctx, id, jobs.JobStatusRunning, jobs.JobStatusCancelRequested, jobs.UpdateStatusFields{CancelRequestedAt: &t})
}

func (f *fakeJobRepo) MarkCanceled(ctx context.Context, id uuid.UUID, finishedAt time.Time) error {
	return f.updateAny(id, []string{jobs.JobStatusCreated, jobs.JobStatusQueued, jobs.JobStatusRunning, jobs.JobStatusCancelRequested}, jobs.JobStatusCanceled, jobs.UpdateStatusFields{FinishedAt: &finishedAt})
}

func (f *fakeJobRepo) IncrementAttempt(ctx context.Context, id uuid.UUID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	job, ok := f.jobs[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "job not found", http.StatusNotFound, nil, nil)
	}
	job.Attempt++
	f.jobs[id] = job
	return nil
}

func (f *fakeJobRepo) updateAny(id uuid.UUID, from []string, to string, fields jobs.UpdateStatusFields) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	job, ok := f.jobs[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "job not found", http.StatusNotFound, nil, nil)
	}
	allowed := false
	for _, status := range from {
		if job.Status == status {
			allowed = true
			break
		}
	}
	if !allowed {
		return httpx.NewAppError(httpx.CodeConflict, "job status changed", http.StatusConflict, nil, nil)
	}
	applyJobStatusFields(&job, to, fields)
	f.jobs[id] = job
	return nil
}

func applyJobStatusFields(job *jobs.Job, status string, fields jobs.UpdateStatusFields) {
	job.Status = status
	job.ErrorMessage = fields.ErrorMessage
	if fields.CancelRequestedAt != nil {
		job.CancelRequestedAt = fields.CancelRequestedAt
	}
	if fields.QueuedAt != nil {
		job.QueuedAt = fields.QueuedAt
	}
	if fields.StartedAt != nil {
		job.StartedAt = fields.StartedAt
	}
	if fields.HeartbeatAt != nil {
		job.HeartbeatAt = fields.HeartbeatAt
	}
	if fields.FinishedAt != nil {
		job.FinishedAt = fields.FinishedAt
	}
	job.UpdatedAt = time.Now().UTC()
}

type fakeQueue struct {
	mu       sync.Mutex
	enqueued []uuid.UUID
	err      error
}

func (f *fakeQueue) Enqueue(ctx context.Context, job jobs.Job) error {
	if f.err != nil {
		return f.err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.enqueued = append(f.enqueued, job.ID)
	return nil
}

func (f *fakeQueue) Close() error {
	return nil
}

type fakeRunEventRepo struct {
	mu        sync.Mutex
	sequences map[string]int64
	events    []runevent.RunEvent
}

func (f *fakeRunEventRepo) Create(ctx context.Context, event runevent.RunEvent) (*runevent.RunEvent, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if event.ID == uuid.Nil {
		event.ID = uuid.New()
	}
	if f.sequences == nil {
		f.sequences = make(map[string]int64)
	}
	key := event.WorkspaceID.String() + "/" + event.RunID.String()
	f.sequences[key]++
	event.Sequence = f.sequences[key]
	if event.CreatedAt.IsZero() {
		event.CreatedAt = time.Now().UTC()
	}
	if event.Payload == nil {
		event.Payload = map[string]any{}
	}
	f.events = append(f.events, event)
	copy := event
	return &copy, nil
}

func (f *fakeRunEventRepo) ListByRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, afterSequence int64, limit int) ([]runevent.RunEvent, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []runevent.RunEvent
	for _, event := range f.events {
		if event.WorkspaceID == workspaceID && event.RunID == runID && event.Sequence > afterSequence {
			out = append(out, event)
		}
	}
	return out, nil
}

func (f *fakeRunEventRepo) LastSequence(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var last int64
	for _, event := range f.events {
		if event.WorkspaceID == workspaceID && event.RunID == runID && event.Sequence > last {
			last = event.Sequence
		}
	}
	return last, nil
}
