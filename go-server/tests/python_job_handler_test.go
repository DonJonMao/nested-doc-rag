package tests

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFillFormPythonHandlerSuccessCallsRunnerAndArchiver(t *testing.T) {
	runDir, manifest := manifestWithArtifacts(t)
	runner := &pythonpkg.FakeRunner{Step15Result: &pythonpkg.Step15RunResult{
		RunID:      uuid.New(),
		OutDir:     runDir,
		Manifest:   manifest,
		Validation: &pythonpkg.ArtifactValidationResult{RunDir: runDir, OK: true},
	}}
	registrar := &fakeArtifactRegistrar{}
	eventsRepo := &fakeRunEventRepo{}
	eventService := runevent.NewService(eventsRepo, nil)
	handler := jobs.NewFillFormPythonHandler(runner, pythonpkg.NewArtifactArchiver(registrar, zap.NewNop()), eventService, zap.NewNop())
	job := fillFormJob(runDir)

	err := handler.Handle(context.Background(), &job)

	require.NoError(t, err)
	require.Len(t, runner.Step15Calls, 1)
	require.Equal(t, "target", runner.Step15Calls[0].TargetNamespace)
	require.Len(t, registrar.requests, 1)
	requireEventTypes(t, eventsRepo, runevent.EventPythonStarted, runevent.EventPythonFinished, runevent.EventArtifactValidationSucceeded, runevent.EventArtifactsRegistered)
}

func TestFillFormPythonHandlerRunnerErrorReturnsError(t *testing.T) {
	runDir := t.TempDir()
	runner := &pythonpkg.FakeRunner{Err: errors.New("python failed")}
	handler := jobs.NewFillFormPythonHandler(runner, pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, nil), runevent.NewService(&fakeRunEventRepo{}, nil), zap.NewNop())
	job := fillFormJob(runDir)

	err := handler.Handle(context.Background(), &job)

	require.Error(t, err)
	require.Contains(t, err.Error(), "python failed")
}

func TestFillFormPythonHandlerArchiverErrorReturnsError(t *testing.T) {
	runDir, manifest := manifestWithArtifacts(t)
	runner := &pythonpkg.FakeRunner{Step15Result: &pythonpkg.Step15RunResult{
		RunID:      uuid.New(),
		OutDir:     runDir,
		Manifest:   manifest,
		Validation: &pythonpkg.ArtifactValidationResult{RunDir: runDir, OK: true},
	}}
	handler := jobs.NewFillFormPythonHandler(runner, pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{err: errors.New("archive failed")}, nil), runevent.NewService(&fakeRunEventRepo{}, nil), zap.NewNop())
	job := fillFormJob(runDir)

	err := handler.Handle(context.Background(), &job)

	require.Error(t, err)
	require.Contains(t, err.Error(), "archive failed")
}

func TestFillFormPythonHandlerValidatesPayload(t *testing.T) {
	handler := jobs.NewFillFormPythonHandler(&pythonpkg.FakeRunner{}, pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, nil), nil, zap.NewNop())
	job := fillFormJob(t.TempDir())
	job.Payload["target_namespace"] = ""

	err := handler.Handle(context.Background(), &job)

	require.Error(t, err)
	require.Contains(t, err.Error(), "target_namespace")
}

func TestIngestKnowledgePythonHandlerDisabled(t *testing.T) {
	handler := jobs.NewIngestKnowledgePythonHandler(&pythonpkg.FakeRunner{}, nil, zap.NewNop(), false)
	job := jobs.Job{ID: uuid.New(), WorkspaceID: uuid.New(), ResourceID: uuid.New(), JobType: jobs.JobTypeIngestKnowledge, Payload: map[string]any{}}

	err := handler.Handle(context.Background(), &job)

	require.ErrorIs(t, err, jobs.ErrHandlerNotImplemented)
}

func TestIngestKnowledgePythonHandlerEnabledCallsRunner(t *testing.T) {
	runner := &pythonpkg.FakeRunner{}
	eventsRepo := &fakeRunEventRepo{}
	handler := jobs.NewIngestKnowledgePythonHandler(runner, runevent.NewService(eventsRepo, nil), zap.NewNop(), true)
	job := jobs.Job{
		ID:          uuid.New(),
		WorkspaceID: uuid.New(),
		ResourceID:  uuid.New(),
		JobType:     jobs.JobTypeIngestKnowledge,
		Payload: map[string]any{
			"input_dir":         "/tmp/input",
			"namespace":         "kb",
			"knowledge_base_id": "kb-1",
			"out_dir":           "/tmp/out",
			"resume":            true,
		},
	}

	err := handler.Handle(context.Background(), &job)

	require.NoError(t, err)
	require.Len(t, runner.IngestCalls, 1)
	require.Equal(t, "kb", runner.IngestCalls[0].Namespace)
	requireEventTypes(t, eventsRepo, runevent.EventPythonStarted, runevent.EventPythonFinished)
}

func fillFormJob(runDir string) jobs.Job {
	return jobs.Job{
		ID:          uuid.New(),
		WorkspaceID: uuid.New(),
		ResourceID:  uuid.New(),
		JobType:     jobs.JobTypeFillForm,
		CreatedBy:   uuid.New(),
		Payload: map[string]any{
			"target_namespace": "target",
			"global_namespace": "global",
			"room_context":     "room",
			"rows":             "4-144",
			"retrieval_mode":   "layered",
			"prompt_version":   "step15_compat",
			"template_path":    filepath.Join(runDir, "template.xlsx"),
			"writeback":        true,
			"resume":           true,
			"out_dir":          runDir,
		},
	}
}

func manifestWithArtifacts(t *testing.T) (string, *pythonpkg.RunManifest) {
	t.Helper()
	runDir := writeTestManifest(t, map[string]string{"predictions": "predictions.json"})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions.json"), []byte("{}"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "template.xlsx"), []byte("template"), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	return runDir, manifest
}

func requireEventTypes(t *testing.T, repo *fakeRunEventRepo, want ...string) {
	t.Helper()
	repo.mu.Lock()
	defer repo.mu.Unlock()
	var got []string
	for _, event := range repo.events {
		got = append(got, event.EventType)
	}
	for _, eventType := range want {
		require.Contains(t, got, eventType)
	}
}
