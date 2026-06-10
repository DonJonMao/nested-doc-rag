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

func TestFillFormHandlerSuccessCallsReviewImporter(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	outDir, manifest := manifestWithArtifacts(t)
	runner := &pythonpkg.FakeRunner{Step15Result: &pythonpkg.Step15RunResult{RunID: runID, OutDir: outDir, Manifest: manifest}}
	eventRepo := &fakeRunEventRepo{}
	importer := &recordingReviewImporter{result: reviewImportResultCompat{TotalParsed: 2, Created: 2, ReviewRequired: 1, WritebackAllowed: 1}}
	handler := jobs.NewFillFormPythonHandler(
		runner,
		pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, zap.NewNop()),
		runevent.NewService(eventRepo, nil),
		zap.NewNop(),
		jobs.WithReviewImporter(importer),
	)
	job := fillFormWorkerTestJob(workspaceID, runID, map[string]any{"writeback": false, "out_dir": outDir})

	err := handler.Handle(context.Background(), &job)

	require.NoError(t, err)
	require.Equal(t, []uuid.UUID{runID}, importer.calls)
	requireEventTypes(t, eventRepo, runevent.EventArtifactsRegistered, runevent.EventReviewItemsImported)
}

func TestFillFormHandlerReviewImportFailureReturnsError(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	outDir, manifest := manifestWithArtifacts(t)
	runner := &pythonpkg.FakeRunner{Step15Result: &pythonpkg.Step15RunResult{RunID: runID, OutDir: outDir, Manifest: manifest}}
	eventRepo := &fakeRunEventRepo{}
	lifecycle := &recordingFillRunLifecycle{}
	importer := &recordingReviewImporter{err: errors.New("import failed")}
	handler := jobs.NewFillFormPythonHandler(
		runner,
		pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, zap.NewNop()),
		runevent.NewService(eventRepo, nil),
		zap.NewNop(),
		jobs.WithFillRunLifecycle(lifecycle),
		jobs.WithReviewImporter(importer),
	)
	job := fillFormWorkerTestJob(workspaceID, runID, map[string]any{"writeback": false, "out_dir": outDir})

	err := handler.Handle(context.Background(), &job)

	require.Error(t, err)
	require.Equal(t, []uuid.UUID{runID}, importer.calls)
	require.Equal(t, []uuid.UUID{runID}, lifecycle.failed)
	require.Empty(t, lifecycle.succeeded)
	requireEventTypes(t, eventRepo, runevent.EventReviewImportFailed)
}
