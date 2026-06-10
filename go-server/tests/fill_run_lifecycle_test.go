package tests

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFillRunLifecycleMarkRunning(t *testing.T) {
	repo := newFakeFillRunRepo()
	runID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: uuid.New(), FormFileID: uuid.New(), Status: formpkg.FillRunStatusQueued}))
	lifecycle := formpkg.NewFillRunLifecycleAdapter(repo, zap.NewNop())

	err := lifecycle.MarkFillRunRunning(context.Background(), runID, uuid.New())

	require.NoError(t, err)
	run, err := repo.GetByID(context.Background(), runID)
	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusRunning, run.Status)
	require.NotNil(t, run.StartedAt)
}

func TestFillRunLifecycleSucceededWithManifestCounts(t *testing.T) {
	repo := newFakeFillRunRepo()
	runID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: uuid.New(), FormFileID: uuid.New(), Status: formpkg.FillRunStatusRunning}))
	lifecycle := formpkg.NewFillRunLifecycleAdapter(repo, zap.NewNop())
	filledArtifactID := uuid.New()
	runDir := writeTestManifest(t, map[string]string{artifact.TypeRunSummary: "summary.json", artifact.TypePredictions: "predictions.json"})
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	manifest.Counts = pythonpkg.ManifestCounts{
		TotalFields:        9,
		Answered:           8,
		PartialClue:        1,
		NotFound:           2,
		ConflictUnresolved: 3,
		ReviewRequired:     4,
		WritebackAllowed:   5,
		Failed:             6,
	}
	result := &pythonpkg.Step15RunResult{OutDir: runDir, Manifest: manifest}

	err = lifecycle.MarkFillRunSucceeded(context.Background(), runID, result, []artifact.RunArtifact{{ID: filledArtifactID, ArtifactType: artifact.TypeFilledForm}})

	require.NoError(t, err)
	run, err := repo.GetByID(context.Background(), runID)
	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusSucceeded, run.Status)
	require.Equal(t, filepath.Join(runDir, pythonpkg.RunManifestFilename), run.RunManifestPath)
	require.Equal(t, filepath.Join(runDir, "summary.json"), run.SummaryPath)
	require.Equal(t, 9, run.ProgressTotal)
	require.Equal(t, 9, run.ProgressDone)
	require.Equal(t, 8, run.AnsweredCount)
	require.Equal(t, 1, run.PartialClueCount)
	require.Equal(t, 2, run.NotFoundCount)
	require.Equal(t, 3, run.ConflictUnresolvedCount)
	require.Equal(t, 4, run.ReviewRequiredCount)
	require.Equal(t, 5, run.WritebackAllowedCount)
	require.Equal(t, 6, run.FailedCount)
	require.NotNil(t, run.FilledFormArtifactID)
	require.Equal(t, filledArtifactID, *run.FilledFormArtifactID)
}

func TestFillRunLifecycleCompletedFailedCanceled(t *testing.T) {
	repo := newFakeFillRunRepo()
	lifecycle := formpkg.NewFillRunLifecycleAdapter(repo, zap.NewNop())
	completedID := uuid.New()
	failedID := uuid.New()
	canceledID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), formpkg.FillRun{ID: completedID, WorkspaceID: uuid.New(), FormFileID: uuid.New(), Status: formpkg.FillRunStatusRunning}))
	require.NoError(t, repo.Create(context.Background(), formpkg.FillRun{ID: failedID, WorkspaceID: uuid.New(), FormFileID: uuid.New(), Status: formpkg.FillRunStatusRunning}))
	require.NoError(t, repo.Create(context.Background(), formpkg.FillRun{ID: canceledID, WorkspaceID: uuid.New(), FormFileID: uuid.New(), Status: formpkg.FillRunStatusRunning}))

	require.NoError(t, lifecycle.MarkFillRunCompletedWithFailures(context.Background(), completedID, &pythonpkg.Step15RunResult{Manifest: &pythonpkg.RunManifest{Counts: pythonpkg.ManifestCounts{Failed: 2}}}, nil, "failed_count=2"))
	require.NoError(t, lifecycle.MarkFillRunFailed(context.Background(), failedID, errors.New("python failed")))
	require.NoError(t, lifecycle.MarkFillRunCanceled(context.Background(), canceledID))

	completed, _ := repo.GetByID(context.Background(), completedID)
	failed, _ := repo.GetByID(context.Background(), failedID)
	canceled, _ := repo.GetByID(context.Background(), canceledID)
	require.Equal(t, formpkg.FillRunStatusCompletedWithFailures, completed.Status)
	require.Equal(t, "failed_count=2", completed.ErrorMessage)
	require.Equal(t, formpkg.FillRunStatusFailed, failed.Status)
	require.Contains(t, failed.ErrorMessage, "python failed")
	require.Equal(t, formpkg.FillRunStatusCanceled, canceled.Status)
}

func TestFillRunLifecycleNilResultAndManifestDoNotPanic(t *testing.T) {
	repo := newFakeFillRunRepo()
	nilResultID := uuid.New()
	nilManifestID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), formpkg.FillRun{ID: nilResultID, WorkspaceID: uuid.New(), FormFileID: uuid.New(), Status: formpkg.FillRunStatusRunning}))
	require.NoError(t, repo.Create(context.Background(), formpkg.FillRun{ID: nilManifestID, WorkspaceID: uuid.New(), FormFileID: uuid.New(), Status: formpkg.FillRunStatusRunning}))
	lifecycle := formpkg.NewFillRunLifecycleAdapter(repo, zap.NewNop())

	require.NotPanics(t, func() {
		require.NoError(t, lifecycle.MarkFillRunSucceeded(context.Background(), nilResultID, nil, nil))
		require.NoError(t, lifecycle.MarkFillRunSucceeded(context.Background(), nilManifestID, &pythonpkg.Step15RunResult{OutDir: t.TempDir()}, nil))
	})

	nilResultRun, err := repo.GetByID(context.Background(), nilResultID)
	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusSucceeded, nilResultRun.Status)
	nilManifestRun, err := repo.GetByID(context.Background(), nilManifestID)
	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusSucceeded, nilManifestRun.Status)
}
