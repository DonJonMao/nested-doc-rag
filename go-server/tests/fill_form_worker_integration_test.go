package tests

import (
	"context"
	"os"
	"path/filepath"
	"testing"

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

func TestFillFormWorkerIntegrationFailureMarksRunFailed(t *testing.T) {
	runID := uuid.New()
	workspaceID := uuid.New()
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusQueued}))
	handler := jobs.NewFillFormPythonHandler(
		&pythonpkg.FakeRunner{},
		pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, zap.NewNop()),
		nil,
		zap.NewNop(),
		jobs.WithFillRunLifecycle(formpkg.NewFillRunLifecycleAdapter(fillRepo, zap.NewNop())),
	)
	job := jobs.Job{ID: uuid.New(), WorkspaceID: workspaceID, ResourceID: runID, JobType: jobs.JobTypeFillForm, Payload: map[string]any{
		"fill_run_id":      runID.String(),
		"target_namespace": "target",
		"writeback":        true,
		"out_dir":          t.TempDir(),
	}}

	err := handler.Handle(context.Background(), &job)

	require.Error(t, err)
	run, getErr := fillRepo.GetByID(context.Background(), runID)
	require.NoError(t, getErr)
	require.Equal(t, formpkg.FillRunStatusFailed, run.Status)
}
