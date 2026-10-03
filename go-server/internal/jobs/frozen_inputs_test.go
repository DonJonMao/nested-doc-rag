package jobs

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/hibiken/asynq"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFrozenBuildSnapshotUsesExactBytesAndRejectsOverrides(t *testing.T) {
	workspace, version, kb := uuid.New(), uuid.New(), uuid.New()
	data := []byte(`{ "schema_version":"kb-build-input-v1", "workspace_id":"` + workspace.String() + `", "knowledge_base_id":"` + kb.String() + `", "index_version_id":"` + version.String() + `", "namespace":"room", "collection":"shared", "documents":[] }`)
	digest := sha256.Sum256(data)
	p := ingestKnowledgePythonPayload{IngestionJobID: uuid.New(), IndexVersionID: version, KnowledgeBaseID: kb.String(), Namespace: "room", QdrantCollection: "shared", OutDir: t.TempDir(), InputSnapshotJSON: string(data), InputSnapshotHash: hex.EncodeToString(digest[:])}
	path, err := freezeBuildSnapshot(p, workspace)
	require.NoError(t, err)
	actual, err := os.ReadFile(path)
	require.NoError(t, err)
	require.Equal(t, data, actual)
	for _, mutate := range []func(*ingestKnowledgePythonPayload){
		func(p *ingestKnowledgePythonPayload) { p.InputSnapshotJSON += "\n" },
		func(p *ingestKnowledgePythonPayload) { p.IndexVersionID = uuid.New() },
		func(p *ingestKnowledgePythonPayload) { p.KnowledgeBaseID = uuid.NewString() },
		func(p *ingestKnowledgePythonPayload) { p.Namespace = "other" },
		func(p *ingestKnowledgePythonPayload) { p.QdrantCollection = "other" },
		func(p *ingestKnowledgePythonPayload) { p.QdrantNamespace = "other" },
		func(p *ingestKnowledgePythonPayload) { p.IndexVersionID = uuid.Nil },
	} {
		bad := p
		mutate(&bad)
		_, err := freezeBuildSnapshot(bad, workspace)
		require.Error(t, err)
		actual, err := os.ReadFile(path)
		require.NoError(t, err)
		require.Equal(t, data, actual)
	}
	_, err = freezeBuildSnapshot(p, uuid.New())
	require.Error(t, err)
}

func TestFrozenFillScopeJSONCannotOverridePins(t *testing.T) {
	workspace := uuid.New()
	target := map[string]any{"collection": "shared", "namespace": "room", "knowledge_base_id": uuid.NewString(), "index_version_id": uuid.NewString(), "storage_contract": "versioned_v1"}
	global := map[string]any{"collection": "shared", "namespace": "global", "knowledge_base_id": uuid.NewString(), "index_version_id": uuid.NewString(), "storage_contract": "versioned_v1"}
	data, err := json.Marshal([]any{target, global})
	require.NoError(t, err)
	targetJSON, _ := json.Marshal(target)
	globalJSON, _ := json.Marshal(global)
	p := fillFormPythonPayload{TargetNamespace: "room", GlobalNamespace: "global", OutDir: t.TempDir(), TargetScope: targetJSON, GlobalScope: globalJSON, IndexScopesJSON: string(data), TemplatePin: json.RawMessage(`{"workspace_id":"` + workspace.String() + `"}`)}
	path, err := freezeFillScopes(p, workspace)
	require.NoError(t, err)
	actual, err := os.ReadFile(path)
	require.NoError(t, err)
	require.Equal(t, data, actual)
	for _, mutate := range []func(*fillFormPythonPayload){
		func(p *fillFormPythonPayload) { p.TargetNamespace = "other" },
		func(p *fillFormPythonPayload) { p.IndexScopesJSON = "[]" },
		func(p *fillFormPythonPayload) { p.TargetScope = p.GlobalScope },
		func(p *fillFormPythonPayload) { p.TemplatePin = nil },
		func(p *fillFormPythonPayload) { p.GlobalScope = nil },
	} {
		bad := p
		mutate(&bad)
		_, err := freezeFillScopes(bad, workspace)
		require.Error(t, err)
	}
	_, err = freezeFillScopes(p, uuid.New())
	require.Error(t, err)
	path, err = freezeFillScopes(fillFormPythonPayload{}, workspace)
	require.NoError(t, err)
	require.Empty(t, path)
}

func TestRetryEnabledFillStartsFreshAndRejectsOrphanCheckpoint(t *testing.T) {
	dir := t.TempDir()
	resume, err := effectiveFillResume(dir, true)
	require.NoError(t, err)
	require.False(t, resume)
	require.NoError(t, os.WriteFile(filepath.Join(dir, "predictions.checkpoint.jsonl"), []byte("{}\n"), 0600))
	_, err = effectiveFillResume(dir, true)
	require.Error(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(dir, "form_input_snapshot.json"), []byte("{}"), 0600))
	resume, err = effectiveFillResume(dir, true)
	require.NoError(t, err)
	require.True(t, resume)
}

type dispatchTestRepo struct {
	Repo
	job         Job
	transitions int
	completed   int
}

func (r *dispatchTestRepo) GetByID(context.Context, uuid.UUID) (*Job, error) {
	copy := r.job
	return &copy, nil
}
func (r *dispatchTestRepo) MarkQueued(_ context.Context, _ uuid.UUID, now time.Time) error {
	r.transitions++
	r.job.Status = JobStatusQueued
	r.job.QueuedAt = &now
	return nil
}
func (r *dispatchTestRepo) ListPendingDispatch(context.Context, int) ([]Job, error) {
	return []Job{r.job}, nil
}
func (r *dispatchTestRepo) MarkPublishedSucceeded(context.Context, uuid.UUID, time.Time) error {
	r.completed++
	r.job.Status = JobStatusSucceeded
	return nil
}

type dispatchTestQueue struct {
	failed bool
	calls  []Job
}

func (q *dispatchTestQueue) Enqueue(_ context.Context, job Job) error {
	q.calls = append(q.calls, job)
	if q.failed {
		return errors.New("redis offline")
	}
	return nil
}
func (q *dispatchTestQueue) Close() error { return nil }

func TestCommittedDispatchRecoversFailureAndUsesPersistedPayload(t *testing.T) {
	repo := &dispatchTestRepo{job: Job{ID: uuid.New(), Status: JobStatusCreated, Payload: map[string]any{"frozen": "original"}}}
	queue := &dispatchTestQueue{failed: true}
	service := NewService(repo, nil, queue, nil, nil, nil, 3)
	forged := repo.job
	forged.Payload = map[string]any{"frozen": "changed"}
	require.Error(t, service.EnqueuePersistedJob(context.Background(), &forged))
	require.Equal(t, JobStatusQueued, repo.job.Status)
	require.Equal(t, "original", queue.calls[0].Payload["frozen"])
	queue.failed = false
	count, err := service.RecoverPendingDispatch(context.Background())
	require.NoError(t, err)
	require.Equal(t, 1, count)
	require.Equal(t, 1, repo.transitions)
	for _, status := range []string{JobStatusCanceled, JobStatusRunning, JobStatusSucceeded, JobStatusFailed} {
		repo.job.Status = status
		require.NoError(t, service.EnqueuePersistedJob(context.Background(), &forged))
	}
	require.Len(t, queue.calls, 2)
}

type publishedTestHandler struct{}

func (publishedTestHandler) Handle(context.Context, *Job) error {
	panic("published index must not rebuild")
}

type pagedDispatchTestRepo struct {
	Repo
	rows []Job
}

func (r *pagedDispatchTestRepo) GetByID(_ context.Context, id uuid.UUID) (*Job, error) {
	for _, job := range r.rows {
		if job.ID == id {
			copy := job
			return &copy, nil
		}
	}
	return nil, errors.New("missing job")
}
func (r *pagedDispatchTestRepo) ListPendingDispatch(ctx context.Context, limit int) ([]Job, error) {
	return r.ListPendingDispatchPage(ctx, nil, uuid.Nil, time.Now().UTC(), limit)
}
func (r *pagedDispatchTestRepo) ListPendingDispatchPage(_ context.Context, after *time.Time, afterID uuid.UUID, cutoff time.Time, limit int) ([]Job, error) {
	var page []Job
	for _, job := range r.rows {
		if job.CreatedAt.After(cutoff) || after != nil && (job.CreatedAt.Before(*after) || job.CreatedAt.Equal(*after) && job.ID.String() <= afterID.String()) {
			continue
		}
		page = append(page, job)
		if len(page) == limit {
			break
		}
	}
	return page, nil
}

func TestPendingDispatchDoesNotStarveJobsBehindFirstFiveHundred(t *testing.T) {
	repo := &pagedDispatchTestRepo{}
	start := time.Now().UTC().Add(-time.Hour)
	for i := 0; i < 601; i++ {
		repo.rows = append(repo.rows, Job{ID: uuid.New(), Status: JobStatusQueued, CreatedAt: start.Add(time.Duration(i) * time.Microsecond)})
	}
	queue := &dispatchTestQueue{}
	service := NewService(repo, nil, queue, nil, nil, nil, 3)
	count, err := service.RecoverPendingDispatch(context.Background())
	require.NoError(t, err)
	require.Equal(t, 601, count)
	require.Len(t, queue.calls, 601)
	require.Equal(t, repo.rows[600].ID, queue.calls[600].ID)
}
func (publishedTestHandler) RecoverPublishedJob(context.Context, *Job) (bool, error) {
	return true, nil
}

func TestWorkerRepairsPublishedRunningAndExhaustedFailedJobWithoutAttempt(t *testing.T) {
	for _, status := range []string{JobStatusRunning, JobStatusFailed, JobStatusQueued, JobStatusCancelRequested, JobStatusCanceled} {
		t.Run(status, func(t *testing.T) {
			repo := &dispatchTestRepo{job: Job{ID: uuid.New(), Status: status, JobType: JobTypeIngestKnowledge, Attempt: 3, MaxAttempts: 3}}
			service := NewService(repo, nil, nil, nil, nil, nil, 3)
			worker := &Worker{repo: repo, service: service, handlers: map[string]TaskHandler{JobTypeIngestKnowledge: publishedTestHandler{}}, logger: zap.NewNop()}
			payload, err := EncodeTaskPayload(repo.job.ID)
			require.NoError(t, err)
			require.NoError(t, worker.ProcessTask(context.Background(), asynq.NewTask("ingest", payload)))
			require.Equal(t, 1, repo.completed)
			require.Equal(t, 3, repo.job.Attempt)
			require.Equal(t, JobStatusSucceeded, repo.job.Status)
		})
	}
}
