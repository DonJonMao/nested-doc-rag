package tests

import (
	"context"
	"errors"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFillRunLifecycleSucceededWithManifestCounts(t *testing.T) {
	repo := newFakeFillRunRepo()
	runID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: uuid.New(), FormFileID: uuid.New(), Status: formpkg.FillRunStatusRunning}))
	lifecycle := formpkg.NewFillRunLifecycleAdapter(repo, zap.NewNop())
	filledArtifactID := uuid.New()
	result := &pythonpkg.Step15RunResult{OutDir: "/tmp/run", Manifest: &pythonpkg.RunManifest{Status: "succeeded", Counts: pythonpkg.ManifestCounts{TotalFields: 3, Answered: 2, Failed: 1}}}

	err := lifecycle.MarkFillRunSucceeded(context.Background(), runID, result, []artifact.RunArtifact{{ID: filledArtifactID, ArtifactType: artifact.TypeFilledForm}})

	require.NoError(t, err)
	run, err := repo.GetByID(context.Background(), runID)
	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusSucceeded, run.Status)
	require.Equal(t, 3, run.ProgressTotal)
	require.Equal(t, 2, run.AnsweredCount)
	require.Equal(t, 1, run.FailedCount)
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
