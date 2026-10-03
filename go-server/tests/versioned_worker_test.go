package tests

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/hibiken/asynq"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestVersionedWorkerUsesFrozenSnapshotAndVersionedCLI(t *testing.T) {
	f := newVersionedWorkerFixture(t)
	require.NoError(t, f.handler.Handle(context.Background(), &f.job))
	require.Empty(t, f.materializer.calls, "the mutable current-document path must not run")
	require.Len(t, f.materializer.frozen, 1)
	require.Equal(t, f.canonical, []byte(f.materializer.frozen[0]))
	require.Equal(t, f.job.WorkspaceID, f.materializer.workspace)
	require.Equal(t, 1, f.materializer.cleanups)
	require.Len(t, f.runner.IngestCalls, 1)
	request := f.runner.IngestCalls[0]
	require.Equal(t, f.version.String(), request.IndexVersionID)
	require.Equal(t, f.snapshot.KnowledgeBaseID.String(), request.KnowledgeBaseID, "use the UUID rather than the display/external ID")
	require.Equal(t, f.materializer.inputDir, request.InputDir)
	require.Equal(t, f.hash, request.InputSnapshotHash)
	require.Equal(t, filepath.Join(f.outDir, "input_snapshot.json"), request.InputSnapshotPath)
	onDisk, err := os.ReadFile(request.InputSnapshotPath)
	require.NoError(t, err)
	require.Equal(t, f.canonical, onDisk, "preserve the exact canonical bytes used by the DB hash")
	spec := (&pythonpkg.CommandBuilder{}).BuildKnowledgeIngestionCommand(request)
	require.Equal(t, f.version.String(), versionedCLIArgument(t, spec.Args, "--index-version-id"))
	require.Equal(t, request.InputSnapshotPath, versionedCLIArgument(t, spec.Args, "--input-snapshot"))
	require.Equal(t, f.hash, versionedCLIArgument(t, spec.Args, "--input-snapshot-hash"))
	require.Equal(t, []uuid.UUID{f.ingestion}, f.lifecycle.running)
	require.Equal(t, []uuid.UUID{f.ingestion}, f.lifecycle.succeeded)
	requireEventTypes(t, f.events, runevent.EventIngestionMaterialized, runevent.EventIndexVersionReady)
}

func TestVersionedWorkerFillUsesFrozenCustomCollectionAndTemplate(t *testing.T) {
	workspace, runID := uuid.New(), uuid.New()
	outDir, manifest := manifestWithArtifacts(t)
	target := knowledge.IndexScope{Collection: "custom_frozen_collection", Namespace: "target", KnowledgeBaseID: uuid.New(), IndexVersionID: uuid.New(), StorageContract: knowledge.StorageVersioned}
	global := knowledge.IndexScope{Collection: target.Collection, Namespace: "global", KnowledgeBaseID: uuid.New(), IndexVersionID: uuid.New(), StorageContract: knowledge.StorageVersioned}
	pin := formpkg.TemplatePin{WorkspaceID: workspace, FileID: uuid.New(), Filename: "frozen.xlsx", ObjectKey: "immutable/template", SHA256: strings.Repeat("a", 64), FileSize: 32}
	run := formpkg.FillRun{ID: runID, WorkspaceID: workspace, TargetNamespace: target.Namespace, GlobalNamespace: global.Namespace, TargetScope: &target, GlobalScope: &global, TemplatePin: &pin, OutDir: outDir}
	payload := formpkg.BuildFillFormJobPayload(run, formpkg.FormFile{ID: uuid.New()}, config.Config{})
	payload["template_path"] = "mutable-template-must-not-be-used.xlsx"
	runner := &pythonpkg.FakeRunner{Step15Result: &pythonpkg.Step15RunResult{RunID: runID, OutDir: outDir, Manifest: manifest}}
	materializer := &versionedRecordingTemplateMaterializer{recordingTemplateMaterializer: recordingTemplateMaterializer{localPath: filepath.Join(outDir, "input", "frozen.xlsx")}}
	handler := jobs.NewFillFormPythonHandler(runner, pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, nil), nil, zap.NewNop(), jobs.WithTemplateMaterializer(materializer))
	job := jobs.Job{ID: uuid.New(), WorkspaceID: workspace, ResourceID: runID, CreatedBy: uuid.New(), JobType: jobs.JobTypeFillForm, Payload: payload}

	require.NoError(t, handler.Handle(context.Background(), &job))
	require.Empty(t, materializer.calls, "the mutable template/current-document path must not run")
	require.Len(t, materializer.pins, 1)
	var receivedPin formpkg.TemplatePin
	require.NoError(t, json.Unmarshal(materializer.pins[0], &receivedPin))
	require.Equal(t, pin, receivedPin)
	require.Equal(t, workspace, materializer.workspace)
	require.Equal(t, 1, materializer.cleanups)
	require.Len(t, runner.Step15Calls, 1)
	request := runner.Step15Calls[0]
	require.Equal(t, target.Collection, request.QdrantCollection, "the frozen collection must override the Python config default")
	require.Equal(t, materializer.localPath, request.TemplatePath)
	require.False(t, request.Resume, "a first dispatch has no frozen form input to resume")
	scopes, err := os.ReadFile(request.IndexScopesPath)
	require.NoError(t, err)
	require.Equal(t, payload["index_scopes_json"], string(scopes))
	var receivedScopes []knowledge.IndexScope
	require.NoError(t, json.Unmarshal(scopes, &receivedScopes))
	require.Equal(t, []knowledge.IndexScope{target, global}, receivedScopes)
	spec := (&pythonpkg.CommandBuilder{}).BuildStep15AgentCommand(request)
	require.Equal(t, target.Collection, versionedCLIArgument(t, spec.Args, "--qdrant-collection"))
	require.Equal(t, request.IndexScopesPath, versionedCLIArgument(t, spec.Args, "--index-scopes"))
}

func TestVersionedWorkerPublishedRetrySkipsAllBuildWork(t *testing.T) {
	f := newVersionedWorkerFixture(t)
	f.lifecycle.published = true
	require.NoError(t, f.handler.Handle(context.Background(), &f.job))
	require.Equal(t, []uuid.UUID{f.ingestion}, f.lifecycle.reads)
	require.Empty(t, f.lifecycle.running)
	require.Empty(t, f.lifecycle.succeeded)
	require.Empty(t, f.lifecycle.failed)
	require.Empty(t, f.runner.IngestCalls)
	require.Empty(t, f.materializer.frozen)
	require.Empty(t, f.materializer.calls)
	require.Empty(t, f.events.events)
	_, err := os.Stat(filepath.Join(f.outDir, "input_snapshot.json"))
	require.True(t, os.IsNotExist(err), "a published retry must not rewrite its input artifacts")
}

func TestVersionedWorkerLifecycleErrorsPropagateWithoutReadyEvent(t *testing.T) {
	for _, phase := range []string{"read", "running", "succeeded"} {
		t.Run(phase, func(t *testing.T) {
			f := newVersionedWorkerFixture(t)
			failure := errors.New("injected " + phase + " lifecycle error")
			switch phase {
			case "read":
				f.lifecycle.readErr = failure
			case "running":
				f.lifecycle.runningErr = failure
			case "succeeded":
				f.lifecycle.succeededErr = failure
			}
			require.ErrorIs(t, f.handler.Handle(context.Background(), &f.job), failure)
			require.NotContains(t, eventTypes(f.events.events), runevent.EventIndexVersionReady)
			if phase != "succeeded" {
				require.Empty(t, f.runner.IngestCalls)
				require.Empty(t, f.materializer.frozen)
				require.Empty(t, f.events.events)
			} else {
				require.Len(t, f.runner.IngestCalls, 1)
				require.Len(t, f.lifecycle.succeeded, 1)
			}
		})
	}
}

func TestVersionedWorkerScopeOrHashMismatchNeverMaterializes(t *testing.T) {
	for _, key := range []string{"input_snapshot_hash", "workspace_id", "knowledge_base_id", "index_version_id", "namespace", "collection"} {
		t.Run(key, func(t *testing.T) {
			f := newVersionedWorkerFixture(t)
			if key == "input_snapshot_hash" {
				f.job.Payload[key] = strings.Repeat("b", 64)
			} else {
				var snapshot map[string]any
				require.NoError(t, json.Unmarshal(f.canonical, &snapshot))
				snapshot[key] = "mismatched frozen input"
				bytes, err := knowledge.CanonicalSnapshotBytes(snapshot)
				require.NoError(t, err)
				f.job.Payload["input_snapshot_json"] = string(bytes)
				f.job.Payload["input_snapshot_hash"] = knowledge.SnapshotBytesHash(bytes)
			}
			require.Error(t, f.handler.Handle(context.Background(), &f.job))
			require.Empty(t, f.runner.IngestCalls)
			require.Empty(t, f.materializer.frozen)
			require.Empty(t, f.materializer.calls)
			require.Equal(t, []uuid.UUID{f.ingestion}, f.lifecycle.failed)
			require.NotContains(t, eventTypes(f.events.events), runevent.EventIndexVersionReady)
		})
	}
}

func TestVersionedWorkerFrozenMaterializationFailurePropagates(t *testing.T) {
	f := newVersionedWorkerFixture(t)
	f.materializer.err = errors.New("frozen source object hash mismatch")
	require.ErrorIs(t, f.handler.Handle(context.Background(), &f.job), f.materializer.err)
	require.Equal(t, 1, f.materializer.cleanups)
	require.Empty(t, f.runner.IngestCalls)
	require.Equal(t, []uuid.UUID{f.ingestion}, f.lifecycle.failed)
	require.NotContains(t, eventTypes(f.events.events), runevent.EventIndexVersionReady)
}

func TestVersionedWorkerRecoversPublishedJobBeforeStatusAndAttempts(t *testing.T) {
	for _, status := range []string{jobs.JobStatusRunning, jobs.JobStatusQueued, jobs.JobStatusFailed, jobs.JobStatusCancelRequested, jobs.JobStatusCanceled} {
		t.Run(status, func(t *testing.T) {
			f := newVersionedWorkerFixture(t)
			f.lifecycle.published = true
			f.job.Status, f.job.Attempt, f.job.MaxAttempts = status, 2, 3
			repo := &versionedPublishedJobRepo{fakeJobRepo: newFakeJobRepo()}
			repo.add(f.job)
			service := jobs.NewService(repo, runevent.NewService(f.events, nil), nil, &fakeAuthorizer{}, nil, zap.NewNop(), 3)
			cfg := workerTestConfig()
			worker := jobs.NewWorker(config.RedisConfig{Addr: "localhost:6379"}, cfg, repo, service, jobs.NewResourceLimiter(cfg), zap.NewNop())
			worker.RegisterHandler(jobs.JobTypeIngestKnowledge, f.handler)
			err := worker.ProcessTask(context.Background(), asynq.NewTask(jobs.TaskType(cfg.RedisNamespace, jobs.JobTypeIngestKnowledge), mustTaskPayload(t, f.job.ID)))
			require.NoError(t, err)
			updated, err := repo.GetByID(context.Background(), f.job.ID)
			require.NoError(t, err)
			require.Equal(t, jobs.JobStatusSucceeded, updated.Status)
			require.Equal(t, 2, updated.Attempt)
			require.Equal(t, 1, repo.publishedWrites)
			require.Empty(t, f.runner.IngestCalls)
			require.Empty(t, f.materializer.frozen)
			require.Empty(t, f.lifecycle.running)
			require.NotContains(t, eventTypes(f.events.events), runevent.EventIndexVersionReady)
			requireEventTypes(t, f.events, runevent.EventSucceeded)
			require.Equal(t, true, f.events.events[len(f.events.events)-1].Payload["publication_recovered"])
		})
	}
}

func TestVersionedWorkerStartupRecoversPublishedBeforeCancelOrFailure(t *testing.T) {
	for _, status := range []string{jobs.JobStatusRunning, jobs.JobStatusCancelRequested} {
		t.Run(status, func(t *testing.T) {
			f := newVersionedWorkerFixture(t)
			f.lifecycle.published = true
			stale := time.Now().Add(-time.Hour)
			f.job.Status, f.job.Attempt, f.job.MaxAttempts, f.job.HeartbeatAt = status, 3, 3, &stale
			repo := &versionedPublishedJobRepo{fakeJobRepo: newFakeJobRepo()}
			repo.add(f.job)
			service := jobs.NewService(repo, runevent.NewService(f.events, nil), nil, &fakeAuthorizer{}, nil, zap.NewNop(), 3)
			cfg := workerTestConfig()
			worker := jobs.NewWorker(config.RedisConfig{Addr: "localhost:6379"}, cfg, repo, service, jobs.NewResourceLimiter(cfg), zap.NewNop())
			worker.RegisterHandler(jobs.JobTypeIngestKnowledge, f.handler)
			count, err := worker.RecoverInterruptedJobs(context.Background(), time.Second)
			require.NoError(t, err)
			require.Equal(t, 1, count)
			updated, err := repo.GetByID(context.Background(), f.job.ID)
			require.NoError(t, err)
			require.Equal(t, jobs.JobStatusSucceeded, updated.Status)
			require.Equal(t, 3, updated.Attempt)
			require.Equal(t, 1, repo.publishedWrites)
			require.Empty(t, f.lifecycle.canceled)
			require.Empty(t, f.lifecycle.failed)
			require.Empty(t, f.runner.IngestCalls)
			require.Empty(t, f.materializer.frozen)
			require.NotContains(t, eventTypes(f.events.events), runevent.EventCanceled)
			require.NotContains(t, eventTypes(f.events.events), runevent.EventFailed)
		})
	}
}

func TestVersionedWorkerPublicationWinsPostHandleCanceledContext(t *testing.T) {
	f := newVersionedWorkerFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	f.job.Status, f.job.MaxAttempts = jobs.JobStatusQueued, 3
	f.lifecycle.afterSuccess = func() {
		f.lifecycle.published = true
		cancel() // actual job context cancellation after the durable publication.
	}
	repo := &versionedPublishedJobRepo{fakeJobRepo: newFakeJobRepo()}
	repo.add(f.job)
	service := jobs.NewService(repo, runevent.NewService(f.events, nil), nil, &fakeAuthorizer{}, nil, zap.NewNop(), 3)
	cfg := workerTestConfig()
	worker := jobs.NewWorker(config.RedisConfig{Addr: "localhost:6379"}, cfg, repo, service, jobs.NewResourceLimiter(cfg), zap.NewNop())
	worker.RegisterHandler(jobs.JobTypeIngestKnowledge, f.handler)
	err := worker.ProcessTask(ctx, asynq.NewTask(jobs.TaskType(cfg.RedisNamespace, jobs.JobTypeIngestKnowledge), mustTaskPayload(t, f.job.ID)))
	require.NoError(t, err)
	require.ErrorIs(t, ctx.Err(), context.Canceled)
	updated, err := repo.GetByID(context.Background(), f.job.ID)
	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusSucceeded, updated.Status)
	require.Equal(t, 1, updated.Attempt)
	require.Equal(t, 1, repo.publishedWrites)
	require.Len(t, f.runner.IngestCalls, 1)
	require.Len(t, f.materializer.frozen, 1)
	require.Empty(t, f.lifecycle.canceled)
	require.NotContains(t, eventTypes(f.events.events), runevent.EventCanceled)
}

type versionedWorkerFixture struct {
	job                jobs.Job
	snapshot           knowledge.BuildInputSnapshot
	canonical          []byte
	hash               string
	outDir             string
	version, ingestion uuid.UUID
	runner             *pythonpkg.FakeRunner
	materializer       *versionedRecordingMaterializer
	lifecycle          *versionedRecordingLifecycle
	events             *fakeRunEventRepo
	handler            *jobs.IngestKnowledgePythonHandler
}

func newVersionedWorkerFixture(t *testing.T) *versionedWorkerFixture {
	t.Helper()
	workspace, kb, version, ingestion, document, file := uuid.New(), uuid.New(), uuid.New(), uuid.New(), uuid.New(), uuid.New()
	f := &versionedWorkerFixture{version: version, ingestion: ingestion, outDir: filepath.Join(t.TempDir(), "run"), lifecycle: &versionedRecordingLifecycle{}, events: &fakeRunEventRepo{}}
	f.snapshot = knowledge.BuildInputSnapshot{SchemaVersion: knowledge.BuildInputSchemaVersion, WorkspaceID: workspace, KnowledgeBaseID: kb, IndexVersionID: version, Collection: "collection", Namespace: "xixian_4", Documents: []knowledge.BuildInputDocument{{DocumentID: document, FileID: file, Filename: "anonymous.docx", RelativePath: document.String() + "/" + file.String() + "/anonymous.docx", ObjectKey: "anonymous/immutable/object", SHA256: strings.Repeat("a", 64), SizeBytes: 32, DocumentRole: knowledge.DocumentRoleKnowledgeBase}}}
	var err error
	f.canonical, err = knowledge.CanonicalSnapshotBytes(f.snapshot)
	require.NoError(t, err)
	f.hash = knowledge.SnapshotBytesHash(f.canonical)
	f.job = ingestJob(workspace, kb, ingestion, version, map[string]any{"input_dir": "mutable-current-path-must-be-ignored", "out_dir": f.outDir, "storage_contract": knowledge.StorageVersioned, "input_snapshot_json": string(f.canonical), "input_snapshot_hash": f.hash, "knowledge_base_id_external": "display-only-id"})
	f.runner = &pythonpkg.FakeRunner{IngestResult: &pythonpkg.IngestionResult{IngestionID: ingestion, OutDir: f.outDir, ManifestPath: filepath.Join(f.outDir, "run_manifest.json")}}
	f.materializer = &versionedRecordingMaterializer{recordingIngestionMaterializer: recordingIngestionMaterializer{inputDir: filepath.Join(f.outDir, version.String()), documentCount: 1}}
	f.handler = jobs.NewIngestKnowledgePythonHandler(f.runner, runevent.NewService(f.events, nil), zap.NewNop(), true, jobs.WithIngestionMaterializer(f.materializer), jobs.WithIngestionLifecycle(f.lifecycle))
	return f
}

type versionedRecordingMaterializer struct {
	recordingIngestionMaterializer
	frozen    []json.RawMessage
	workspace uuid.UUID
	cleanups  int
}

func (m *versionedRecordingMaterializer) MaterializeBuildInput(_ context.Context, workspace uuid.UUID, snapshot json.RawMessage, _ string) (string, int, func(), error) {
	m.workspace = workspace
	m.frozen = append(m.frozen, append(json.RawMessage(nil), snapshot...))
	return m.inputDir, m.documentCount, func() { m.cleanups++ }, m.err
}

type versionedRecordingLifecycle struct {
	recordingIngestionLifecycle
	published                         bool
	reads                             []uuid.UUID
	readErr, runningErr, succeededErr error
	afterSuccess                      func()
}

type versionedRecordingTemplateMaterializer struct {
	recordingTemplateMaterializer
	pins      []json.RawMessage
	workspace uuid.UUID
	cleanups  int
}

func (m *versionedRecordingTemplateMaterializer) MaterializePinnedTemplate(_ context.Context, workspace uuid.UUID, pin json.RawMessage, _ string) (string, func(), error) {
	m.workspace = workspace
	m.pins = append(m.pins, append(json.RawMessage(nil), pin...))
	return m.localPath, func() { m.cleanups++ }, m.err
}

func (l *versionedRecordingLifecycle) ReadPublishedIngestion(_ context.Context, id uuid.UUID) (*pythonpkg.IngestionResult, bool, error) {
	l.reads = append(l.reads, id)
	return &pythonpkg.IngestionResult{IngestionID: id}, l.published, l.readErr
}
func (l *versionedRecordingLifecycle) MarkIngestionRunning(ctx context.Context, id, job uuid.UUID) error {
	_ = l.recordingIngestionLifecycle.MarkIngestionRunning(ctx, id, job)
	return l.runningErr
}
func (l *versionedRecordingLifecycle) MarkIngestionSucceeded(ctx context.Context, id uuid.UUID, result *pythonpkg.IngestionResult) error {
	_ = l.recordingIngestionLifecycle.MarkIngestionSucceeded(ctx, id, result)
	if l.succeededErr == nil && l.afterSuccess != nil {
		l.afterSuccess()
	}
	return l.succeededErr
}

type versionedPublishedJobRepo struct {
	*fakeJobRepo
	publishedWrites int
}

func (r *versionedPublishedJobRepo) MarkPublishedSucceeded(_ context.Context, id uuid.UUID, finished time.Time) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.publishedWrites++
	job := r.jobs[id]
	job.Status, job.FinishedAt = jobs.JobStatusSucceeded, &finished
	r.jobs[id] = job
	return nil
}
func versionedCLIArgument(t *testing.T, args []string, key string) string {
	t.Helper()
	for i, arg := range args {
		if arg == key && i+1 < len(args) {
			return args[i+1]
		}
	}
	t.Fatalf("missing argument %s in %v", key, args)
	return ""
}
