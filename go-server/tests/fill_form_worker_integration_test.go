package tests

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFillFormWorkerIntegrationMaterializesTemplateAndUpdatesRun(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	formID := uuid.New()
	fileID := uuid.New()
	outDir, manifest := manifestWithArtifacts(t)
	formRepo := newFakeFormFileRepo()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: fileID, Filename: "template.xlsx"}))
	fileRepo := newFakeFileRepo()
	fileRepo.files[fileID] = filepkg.File{ID: fileID, WorkspaceID: workspaceID, Filename: "template.xlsx", ObjectKey: "templates/template.xlsx", FileCategory: filepkg.FileCategoryFormTemplate, Status: filepkg.FileStatusActive}
	storage := newFakeObjectStorage()
	storage.objects["templates/template.xlsx"] = []byte("template")
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: formID, Status: formpkg.FillRunStatusQueued}))
	runner := &pythonpkg.FakeRunner{Step15Result: &pythonpkg.Step15RunResult{RunID: runID, OutDir: outDir, Manifest: manifest, Validation: &pythonpkg.ArtifactValidationResult{RunDir: outDir, OK: true}}}
	registrar := &fakeArtifactRegistrar{}
	eventRepo := &fakeRunEventRepo{}
	handler := jobs.NewFillFormPythonHandler(
		runner,
		pythonpkg.NewArtifactArchiver(registrar, zap.NewNop()),
		runevent.NewService(eventRepo, nil),
		zap.NewNop(),
		jobs.WithTemplateMaterializer(formpkg.NewTemplateMaterializer(formRepo, fileRepo, storage, zap.NewNop())),
		jobs.WithFillRunLifecycle(formpkg.NewFillRunLifecycleAdapter(fillRepo, zap.NewNop())),
	)
	job := jobs.Job{
		ID:          uuid.New(),
		WorkspaceID: workspaceID,
		ResourceID:  runID,
		JobType:     jobs.JobTypeFillForm,
		CreatedBy:   uuid.New(),
		Payload: map[string]any{
			"fill_run_id":      runID.String(),
			"workspace_id":     workspaceID.String(),
			"form_file_id":     formID.String(),
			"target_namespace": "target",
			"rows":             "4-144",
			"retrieval_mode":   "layered",
			"prompt_version":   "step15_compat",
			"writeback":        true,
			"out_dir":          outDir,
		},
	}

	err := handler.Handle(context.Background(), &job)

	require.NoError(t, err)
	require.Len(t, runner.Step15Calls, 1)
	templatePath := runner.Step15Calls[0].TemplatePath
	require.NotEmpty(t, templatePath)
	require.Equal(t, filepath.Join(outDir, "input", "template.xlsx"), templatePath)
	data, err := os.ReadFile(templatePath)
	require.NoError(t, err)
	require.Equal(t, []byte("template"), data)
	require.Len(t, registrar.requests, 1)
	run, err := fillRepo.GetByID(context.Background(), runID)
	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusSucceeded, run.Status)
	requireEventTypes(t, eventRepo, runevent.EventPythonStarted, runevent.EventPythonFinished, runevent.EventArtifactsRegistered)
}

func TestFillFormWorkerIntegrationMaterializerFailureMarksRunFailed(t *testing.T) {
	runID := uuid.New()
	workspaceID := uuid.New()
	runner := &pythonpkg.FakeRunner{}
	lifecycle := &recordingFillRunLifecycle{}
	handler := jobs.NewFillFormPythonHandler(
		runner,
		pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, zap.NewNop()),
		nil,
		zap.NewNop(),
		jobs.WithTemplateMaterializer(&recordingTemplateMaterializer{err: errors.New("storage missing")}),
		jobs.WithFillRunLifecycle(lifecycle),
	)
	job := jobs.Job{ID: uuid.New(), WorkspaceID: workspaceID, ResourceID: runID, JobType: jobs.JobTypeFillForm, Payload: map[string]any{
		"fill_run_id":      runID.String(),
		"form_file_id":     uuid.NewString(),
		"target_namespace": "target",
		"writeback":        true,
		"out_dir":          t.TempDir(),
	}}

	err := handler.Handle(context.Background(), &job)

	require.Error(t, err)
	require.Empty(t, runner.Step15Calls)
	require.Equal(t, []uuid.UUID{runID}, lifecycle.failed)
	require.Empty(t, lifecycle.running)
}

func TestFillFormWorkerIntegrationRunnerFailureMarksRunFailed(t *testing.T) {
	runID := uuid.New()
	workspaceID := uuid.New()
	runner := &pythonpkg.FakeRunner{Step15Err: errors.New("python failed")}
	lifecycle := &recordingFillRunLifecycle{}
	eventRepo := &fakeRunEventRepo{}
	handler := jobs.NewFillFormPythonHandler(
		runner,
		pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, zap.NewNop()),
		runevent.NewService(eventRepo, nil),
		zap.NewNop(),
		jobs.WithFillRunLifecycle(lifecycle),
	)
	job := fillFormWorkerTestJob(workspaceID, runID, map[string]any{"writeback": false})

	err := handler.Handle(context.Background(), &job)

	require.Error(t, err)
	require.Len(t, runner.Step15Calls, 1)
	require.Equal(t, []uuid.UUID{runID}, lifecycle.running)
	require.Equal(t, []uuid.UUID{runID}, lifecycle.failed)
	requireEventTypes(t, eventRepo, runevent.EventPythonStarted, runevent.EventArtifactValidationFailed)
}

func TestFillFormWorkerIntegrationArchiverFailureMarksRunFailed(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	outDir, manifest := manifestWithArtifacts(t)
	runner := &pythonpkg.FakeRunner{Step15Result: &pythonpkg.Step15RunResult{RunID: runID, OutDir: outDir, Manifest: manifest}}
	lifecycle := &recordingFillRunLifecycle{}
	handler := jobs.NewFillFormPythonHandler(
		runner,
		pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{err: errors.New("register failed")}, zap.NewNop()),
		nil,
		zap.NewNop(),
		jobs.WithFillRunLifecycle(lifecycle),
	)
	job := fillFormWorkerTestJob(workspaceID, runID, map[string]any{"writeback": false, "out_dir": outDir})

	err := handler.Handle(context.Background(), &job)

	require.Error(t, err)
	require.Equal(t, []uuid.UUID{runID}, lifecycle.running)
	require.Equal(t, []uuid.UUID{runID}, lifecycle.failed)
	require.Empty(t, lifecycle.succeeded)
}

func TestFillFormWorkerIntegrationCompletedWithFailuresLifecycle(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	outDir, manifest := manifestWithArtifacts(t)
	manifest.Status = jobs.JobStatusCompletedWithFailures
	manifest.Counts.Failed = 2
	runner := &pythonpkg.FakeRunner{Step15Result: &pythonpkg.Step15RunResult{RunID: runID, OutDir: outDir, Manifest: manifest, Validation: &pythonpkg.ArtifactValidationResult{RunDir: outDir, OK: true}}}
	lifecycle := &recordingFillRunLifecycle{}
	handler := jobs.NewFillFormPythonHandler(
		runner,
		pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, zap.NewNop()),
		nil,
		zap.NewNop(),
		jobs.WithFillRunLifecycle(lifecycle),
	)
	job := fillFormWorkerTestJob(workspaceID, runID, map[string]any{"writeback": false, "out_dir": outDir})

	err := handler.Handle(context.Background(), &job)

	require.NoError(t, err)
	require.Equal(t, []uuid.UUID{runID}, lifecycle.running)
	require.Equal(t, []uuid.UUID{runID}, lifecycle.completedWithFailures)
	require.Empty(t, lifecycle.succeeded)
	require.Len(t, lifecycle.completedArtifacts[runID], 1)
}

func TestFillFormWorkerIntegrationContextCanceledMarksRunCanceled(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	runner := &pythonpkg.FakeRunner{Delay: time.Hour}
	lifecycle := &recordingFillRunLifecycle{}
	handler := jobs.NewFillFormPythonHandler(
		runner,
		pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, zap.NewNop()),
		nil,
		zap.NewNop(),
		jobs.WithFillRunLifecycle(lifecycle),
	)
	job := fillFormWorkerTestJob(workspaceID, runID, map[string]any{"writeback": false})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	err := handler.Handle(ctx, &job)

	require.Error(t, err)
	require.Equal(t, []uuid.UUID{runID}, lifecycle.running)
	require.Equal(t, []uuid.UUID{runID}, lifecycle.canceled)
	require.Empty(t, lifecycle.failed)
}

func fillFormWorkerTestJob(workspaceID uuid.UUID, runID uuid.UUID, overrides map[string]any) jobs.Job {
	payload := map[string]any{
		"fill_run_id":      runID.String(),
		"workspace_id":     workspaceID.String(),
		"form_file_id":     uuid.NewString(),
		"target_namespace": "target",
		"global_namespace": "global",
		"rows":             "4-144",
		"retrieval_mode":   "layered",
		"prompt_version":   "step15_compat",
		"writeback":        true,
		"out_dir":          filepath.Join(os.TempDir(), runID.String()),
	}
	for key, value := range overrides {
		payload[key] = value
	}
	return jobs.Job{ID: uuid.New(), WorkspaceID: workspaceID, ResourceID: runID, JobType: jobs.JobTypeFillForm, CreatedBy: uuid.New(), Payload: payload}
}

type recordingTemplateMaterializer struct {
	localPath string
	err       error
	calls     []uuid.UUID
}

func (m *recordingTemplateMaterializer) MaterializeTemplate(ctx context.Context, workspaceID uuid.UUID, formFileID uuid.UUID, outDir string) (string, func(), error) {
	m.calls = append(m.calls, formFileID)
	if m.err != nil {
		return "", func() {}, m.err
	}
	if m.localPath != "" {
		return m.localPath, func() {}, nil
	}
	return filepath.Join(outDir, "input", "template.xlsx"), func() {}, nil
}

type recordingFillRunLifecycle struct {
	running               []uuid.UUID
	succeeded             []uuid.UUID
	completedWithFailures []uuid.UUID
	failed                []uuid.UUID
	canceled              []uuid.UUID
	completedArtifacts    map[uuid.UUID][]artifact.RunArtifact
}

func (l *recordingFillRunLifecycle) MarkFillRunRunning(ctx context.Context, runID uuid.UUID, jobID uuid.UUID) error {
	l.running = append(l.running, runID)
	return nil
}

func (l *recordingFillRunLifecycle) MarkFillRunSucceeded(ctx context.Context, runID uuid.UUID, result *pythonpkg.Step15RunResult, artifacts []artifact.RunArtifact) error {
	l.succeeded = append(l.succeeded, runID)
	return nil
}

func (l *recordingFillRunLifecycle) MarkFillRunCompletedWithFailures(ctx context.Context, runID uuid.UUID, result *pythonpkg.Step15RunResult, artifacts []artifact.RunArtifact, errMsg string) error {
	l.completedWithFailures = append(l.completedWithFailures, runID)
	if l.completedArtifacts == nil {
		l.completedArtifacts = make(map[uuid.UUID][]artifact.RunArtifact)
	}
	l.completedArtifacts[runID] = append([]artifact.RunArtifact(nil), artifacts...)
	return nil
}

func (l *recordingFillRunLifecycle) MarkFillRunFailed(ctx context.Context, runID uuid.UUID, err error) error {
	l.failed = append(l.failed, runID)
	return nil
}

func (l *recordingFillRunLifecycle) MarkFillRunCanceled(ctx context.Context, runID uuid.UUID) error {
	l.canceled = append(l.canceled, runID)
	return nil
}
