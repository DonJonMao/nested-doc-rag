package tests

import (
	"context"
	"errors"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestIngestWorkerMaterializesAndRunsPython(t *testing.T) {
	workspaceID := uuid.New()
	kbID := uuid.New()
	ingestionID := uuid.New()
	versionID := uuid.New()
	runner := &pythonpkg.FakeRunner{IngestResult: &pythonpkg.IngestionResult{IngestionID: ingestionID, OutDir: "/tmp/out", ManifestPath: "/tmp/out/run_manifest.json"}}
	materializer := &recordingIngestionMaterializer{inputDir: "/tmp/out/input", documentCount: 2}
	lifecycle := &recordingIngestionLifecycle{}
	events := &fakeRunEventRepo{}
	handler := jobs.NewIngestKnowledgePythonHandler(
		runner,
		runevent.NewService(events, nil),
		zap.NewNop(),
		true,
		jobs.WithIngestionMaterializer(materializer),
		jobs.WithIngestionLifecycle(lifecycle),
	)
	job := ingestJob(workspaceID, kbID, ingestionID, versionID, map[string]any{"input_dir": ""})

	err := handler.Handle(context.Background(), &job)

	require.NoError(t, err)
	require.Equal(t, []uuid.UUID{kbID}, materializer.calls)
	require.Len(t, runner.IngestCalls, 1)
	require.Equal(t, "/tmp/out/input", runner.IngestCalls[0].InputDir)
	require.Equal(t, ingestionID, runner.IngestCalls[0].IngestionID)
	require.Equal(t, []uuid.UUID{ingestionID}, lifecycle.running)
	require.Equal(t, []uuid.UUID{ingestionID}, lifecycle.succeeded)
	requireEventTypes(t, events, runevent.EventIngestionStarted, runevent.EventIngestionMaterialized, runevent.EventIngestionFinished, runevent.EventIndexVersionReady)
}

func TestIngestWorkerDisabledMarksFailed(t *testing.T) {
	workspaceID := uuid.New()
	kbID := uuid.New()
	ingestionID := uuid.New()
	lifecycle := &recordingIngestionLifecycle{}
	handler := jobs.NewIngestKnowledgePythonHandler(&pythonpkg.FakeRunner{}, nil, zap.NewNop(), false, jobs.WithIngestionLifecycle(lifecycle))
	job := ingestJob(workspaceID, kbID, ingestionID, uuid.New(), nil)

	err := handler.Handle(context.Background(), &job)

	require.ErrorIs(t, err, jobs.ErrHandlerNotImplemented)
	require.Equal(t, []uuid.UUID{ingestionID}, lifecycle.failed)
}

func TestIngestWorkerRunnerFailureMarksFailed(t *testing.T) {
	workspaceID := uuid.New()
	kbID := uuid.New()
	ingestionID := uuid.New()
	runner := &pythonpkg.FakeRunner{IngestErr: errors.New("python failed")}
	lifecycle := &recordingIngestionLifecycle{}
	handler := jobs.NewIngestKnowledgePythonHandler(runner, nil, zap.NewNop(), true, jobs.WithIngestionLifecycle(lifecycle))
	job := ingestJob(workspaceID, kbID, ingestionID, uuid.New(), map[string]any{"input_dir": "/tmp/input"})

	err := handler.Handle(context.Background(), &job)

	require.Error(t, err)
	require.Equal(t, []uuid.UUID{ingestionID}, lifecycle.running)
	require.Equal(t, []uuid.UUID{ingestionID}, lifecycle.failed)
}

func ingestJob(workspaceID uuid.UUID, kbID uuid.UUID, ingestionID uuid.UUID, versionID uuid.UUID, overrides map[string]any) jobs.Job {
	payload := map[string]any{
		"ingestion_job_id":           ingestionID.String(),
		"workspace_id":               workspaceID.String(),
		"knowledge_base_id":          kbID.String(),
		"index_version_id":           versionID.String(),
		"config_path":                "config/local.yaml",
		"input_dir":                  "/tmp/input",
		"namespace":                  "xixian_4",
		"knowledge_base_id_external": kbID.String(),
		"out_dir":                    "/tmp/out",
		"resume":                     true,
		"qdrant_collection":          "collection",
		"qdrant_namespace":           "xixian_4",
	}
	for key, value := range overrides {
		payload[key] = value
	}
	return jobs.Job{ID: uuid.New(), WorkspaceID: workspaceID, ResourceID: ingestionID, JobType: jobs.JobTypeIngestKnowledge, CreatedBy: uuid.New(), Payload: payload}
}

type recordingIngestionMaterializer struct {
	inputDir      string
	documentCount int
	err           error
	calls         []uuid.UUID
}

func (m *recordingIngestionMaterializer) MaterializeDocuments(ctx context.Context, workspaceID uuid.UUID, knowledgeBaseID uuid.UUID, outDir string) (string, int, func(), error) {
	m.calls = append(m.calls, knowledgeBaseID)
	if m.err != nil {
		return "", 0, func() {}, m.err
	}
	return m.inputDir, m.documentCount, func() {}, nil
}

type recordingIngestionLifecycle struct {
	running   []uuid.UUID
	succeeded []uuid.UUID
	failed    []uuid.UUID
	canceled  []uuid.UUID
}

func (l *recordingIngestionLifecycle) MarkIngestionRunning(ctx context.Context, ingestionJobID uuid.UUID, jobID uuid.UUID) error {
	l.running = append(l.running, ingestionJobID)
	return nil
}

func (l *recordingIngestionLifecycle) MarkIngestionSucceeded(ctx context.Context, ingestionJobID uuid.UUID, result *pythonpkg.IngestionResult) error {
	l.succeeded = append(l.succeeded, ingestionJobID)
	return nil
}

func (l *recordingIngestionLifecycle) MarkIngestionFailed(ctx context.Context, ingestionJobID uuid.UUID, err error) error {
	l.failed = append(l.failed, ingestionJobID)
	return nil
}

func (l *recordingIngestionLifecycle) MarkIngestionCanceled(ctx context.Context, ingestionJobID uuid.UUID) error {
	l.canceled = append(l.canceled, ingestionJobID)
	return nil
}
