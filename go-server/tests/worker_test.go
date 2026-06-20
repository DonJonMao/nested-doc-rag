package tests

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/hibiken/asynq"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestWorkerNoopSuccessMarksSucceededAndHeartbeat(t *testing.T) {
	repo := newFakeJobRepo()
	eventRepo := &fakeRunEventRepo{}
	service := jobs.NewService(repo, runevent.NewService(eventRepo, nil), nil, &fakeAuthorizer{}, nil, zap.NewNop(), 3)
	cfg := workerTestConfig()
	cfg.HeartbeatInterval = config.NewDuration(5 * time.Millisecond)
	worker := jobs.NewWorker(config.RedisConfig{Addr: "localhost:6379"}, cfg, repo, service, jobs.NewResourceLimiter(cfg), zap.NewNop())
	worker.RegisterDefaultHandlers(runevent.NewService(eventRepo, nil))
	job := jobs.Job{ID: uuid.New(), WorkspaceID: uuid.New(), JobType: jobs.JobTypeNoop, ResourceType: jobs.ResourceTypeNoop, ResourceID: uuid.New(), Status: jobs.JobStatusQueued, MaxAttempts: 3, Payload: map[string]any{"sleep_ms": 25}}
	repo.add(job)

	err := worker.ProcessTask(context.Background(), asynq.NewTask(jobs.TaskType(cfg.RedisNamespace, jobs.JobTypeNoop), mustTaskPayload(t, job.ID)))

	require.NoError(t, err)
	updated, err := repo.GetByID(context.Background(), job.ID)
	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusSucceeded, updated.Status)
	require.NotNil(t, updated.HeartbeatAt)
	require.Contains(t, eventTypes(eventRepo.events), runevent.EventSucceeded)
	require.Contains(t, eventTypes(eventRepo.events), runevent.EventProgress)
}

func TestWorkerPlaceholderMarksFailed(t *testing.T) {
	repo := newFakeJobRepo()
	service := jobs.NewService(repo, runevent.NewService(&fakeRunEventRepo{}, nil), nil, &fakeAuthorizer{}, nil, zap.NewNop(), 1)
	cfg := workerTestConfig()
	worker := jobs.NewWorker(config.RedisConfig{Addr: "localhost:6379"}, cfg, repo, service, jobs.NewResourceLimiter(cfg), zap.NewNop())
	worker.RegisterDefaultHandlers(nil)
	job := jobs.Job{ID: uuid.New(), WorkspaceID: uuid.New(), JobType: jobs.JobTypeFillForm, ResourceType: jobs.ResourceTypeFillRun, ResourceID: uuid.New(), Status: jobs.JobStatusQueued, MaxAttempts: 1, Payload: map[string]any{}}
	repo.add(job)

	err := worker.ProcessTask(context.Background(), asynq.NewTask(jobs.TaskType(cfg.RedisNamespace, jobs.JobTypeFillForm), mustTaskPayload(t, job.ID)))

	require.Error(t, err)
	require.True(t, errors.Is(err, asynq.SkipRetry))
	updated, err := repo.GetByID(context.Background(), job.ID)
	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusFailed, updated.Status)
	require.Contains(t, updated.ErrorMessage, "not implemented in Block 3")
}

func TestWorkerRetryableFailureReturnsErrorForAsynqRetry(t *testing.T) {
	repo := newFakeJobRepo()
	service := jobs.NewService(repo, runevent.NewService(&fakeRunEventRepo{}, nil), nil, &fakeAuthorizer{}, nil, zap.NewNop(), 3)
	cfg := workerTestConfig()
	cfg.MaxAttempts = 3
	worker := jobs.NewWorker(config.RedisConfig{Addr: "localhost:6379"}, cfg, repo, service, jobs.NewResourceLimiter(cfg), zap.NewNop())
	worker.RegisterHandler(jobs.JobTypeNoop, failingTaskHandler{})
	job := jobs.Job{ID: uuid.New(), WorkspaceID: uuid.New(), JobType: jobs.JobTypeNoop, ResourceType: jobs.ResourceTypeNoop, ResourceID: uuid.New(), Status: jobs.JobStatusQueued, MaxAttempts: 3, Payload: map[string]any{}}
	repo.add(job)

	err := worker.ProcessTask(context.Background(), asynq.NewTask(jobs.TaskType(cfg.RedisNamespace, jobs.JobTypeNoop), mustTaskPayload(t, job.ID)))

	require.Error(t, err)
	require.False(t, errors.Is(err, asynq.SkipRetry))
	updated, getErr := repo.GetByID(context.Background(), job.ID)
	require.NoError(t, getErr)
	require.Equal(t, jobs.JobStatusFailed, updated.Status)
	require.Equal(t, 1, updated.Attempt)
}

func TestWorkerCanceledJobSkippedAndFailureDoesNotKillNextJob(t *testing.T) {
	repo := newFakeJobRepo()
	service := jobs.NewService(repo, runevent.NewService(&fakeRunEventRepo{}, nil), nil, &fakeAuthorizer{}, nil, zap.NewNop(), 1)
	cfg := workerTestConfig()
	worker := jobs.NewWorker(config.RedisConfig{Addr: "localhost:6379"}, cfg, repo, service, jobs.NewResourceLimiter(cfg), zap.NewNop())
	worker.RegisterDefaultHandlers(nil)
	canceled := jobs.Job{ID: uuid.New(), WorkspaceID: uuid.New(), JobType: jobs.JobTypeNoop, ResourceType: jobs.ResourceTypeNoop, ResourceID: uuid.New(), Status: jobs.JobStatusCanceled, MaxAttempts: 1, Payload: map[string]any{}}
	failing := jobs.Job{ID: uuid.New(), WorkspaceID: uuid.New(), JobType: jobs.JobTypeArchiveArtifacts, ResourceType: jobs.ResourceTypeArtifactArchive, ResourceID: uuid.New(), Status: jobs.JobStatusQueued, MaxAttempts: 1, Payload: map[string]any{}}
	success := jobs.Job{ID: uuid.New(), WorkspaceID: uuid.New(), JobType: jobs.JobTypeNoop, ResourceType: jobs.ResourceTypeNoop, ResourceID: uuid.New(), Status: jobs.JobStatusQueued, MaxAttempts: 1, Payload: map[string]any{}}
	repo.add(canceled)
	repo.add(failing)
	repo.add(success)

	require.NoError(t, worker.ProcessTask(context.Background(), asynq.NewTask(jobs.TaskType(cfg.RedisNamespace, jobs.JobTypeNoop), mustTaskPayload(t, canceled.ID))))
	require.Error(t, worker.ProcessTask(context.Background(), asynq.NewTask(jobs.TaskType(cfg.RedisNamespace, jobs.JobTypeArchiveArtifacts), mustTaskPayload(t, failing.ID))))
	require.NoError(t, worker.ProcessTask(context.Background(), asynq.NewTask(jobs.TaskType(cfg.RedisNamespace, jobs.JobTypeNoop), mustTaskPayload(t, success.ID))))

	updated, err := repo.GetByID(context.Background(), success.ID)
	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusSucceeded, updated.Status)
}

func TestWorkerRecoverInterruptedJobsMarksStaleJobs(t *testing.T) {
	repo := newFakeJobRepo()
	eventRepo := &fakeRunEventRepo{}
	service := jobs.NewService(repo, runevent.NewService(eventRepo, nil), nil, &fakeAuthorizer{}, nil, zap.NewNop(), 1)
	cfg := workerTestConfig()
	worker := jobs.NewWorker(config.RedisConfig{Addr: "localhost:6379"}, cfg, repo, service, jobs.NewResourceLimiter(cfg), zap.NewNop())
	handler := &recordingRecoveryHandler{}
	worker.RegisterHandler(jobs.JobTypeFillForm, handler)
	now := time.Now().UTC()
	stale := now.Add(-5 * time.Minute)
	fresh := now
	staleRunning := jobs.Job{ID: uuid.New(), WorkspaceID: uuid.New(), JobType: jobs.JobTypeFillForm, ResourceType: jobs.ResourceTypeFillRun, ResourceID: uuid.New(), Status: jobs.JobStatusRunning, MaxAttempts: 1, Payload: map[string]any{}, HeartbeatAt: &stale}
	staleCancel := jobs.Job{ID: uuid.New(), WorkspaceID: uuid.New(), JobType: jobs.JobTypeFillForm, ResourceType: jobs.ResourceTypeFillRun, ResourceID: uuid.New(), Status: jobs.JobStatusCancelRequested, MaxAttempts: 1, Payload: map[string]any{}, HeartbeatAt: &stale}
	freshRunning := jobs.Job{ID: uuid.New(), WorkspaceID: uuid.New(), JobType: jobs.JobTypeFillForm, ResourceType: jobs.ResourceTypeFillRun, ResourceID: uuid.New(), Status: jobs.JobStatusRunning, MaxAttempts: 1, Payload: map[string]any{}, HeartbeatAt: &fresh}
	repo.add(staleRunning)
	repo.add(staleCancel)
	repo.add(freshRunning)

	recovered, err := worker.RecoverInterruptedJobs(context.Background(), time.Minute)

	require.NoError(t, err)
	require.Equal(t, 2, recovered)
	updatedRunning, err := repo.GetByID(context.Background(), staleRunning.ID)
	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusFailed, updatedRunning.Status)
	require.Contains(t, updatedRunning.ErrorMessage, "worker interrupted")
	updatedCancel, err := repo.GetByID(context.Background(), staleCancel.ID)
	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusCanceled, updatedCancel.Status)
	updatedFresh, err := repo.GetByID(context.Background(), freshRunning.ID)
	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusRunning, updatedFresh.Status)
	require.ElementsMatch(t, []string{jobs.JobStatusFailed, jobs.JobStatusCanceled}, handler.statuses)
	require.Contains(t, eventTypes(eventRepo.events), runevent.EventFailed)
	require.Contains(t, eventTypes(eventRepo.events), runevent.EventCanceled)
}

type failingTaskHandler struct{}

func (failingTaskHandler) Handle(ctx context.Context, job *jobs.Job) error {
	return errors.New("transient failure")
}

type recordingRecoveryHandler struct {
	statuses []string
}

func (h *recordingRecoveryHandler) Handle(ctx context.Context, job *jobs.Job) error {
	return nil
}

func (h *recordingRecoveryHandler) RecoverInterruptedJob(ctx context.Context, job *jobs.Job, terminalStatus string, err error) {
	h.statuses = append(h.statuses, terminalStatus)
}

func workerTestConfig() config.JobsConfig {
	return config.JobsConfig{
		FillConcurrency:      1,
		IngestionConcurrency: 1,
		MaxPythonProcesses:   1,
		RedisNamespace:       "test",
		WorkerConcurrency:    1,
		DefaultTimeout:       config.NewDuration(time.Minute),
		MaxAttempts:          1,
		RetryBackoff:         config.NewDuration(time.Millisecond),
		HeartbeatInterval:    config.NewDuration(time.Second),
		EventBufferSize:      8,
	}
}

func mustTaskPayload(t *testing.T, jobID uuid.UUID) []byte {
	t.Helper()
	payload, err := jobs.EncodeTaskPayload(jobID)
	require.NoError(t, err)
	return payload
}

func eventTypes(events []runevent.RunEvent) []string {
	out := make([]string, 0, len(events))
	for _, event := range events {
		out = append(out, event.EventType)
	}
	return out
}
