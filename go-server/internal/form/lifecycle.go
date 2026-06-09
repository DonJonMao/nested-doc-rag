package form

import (
	"context"
	"fmt"
	"path/filepath"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type FillRunLifecycleAdapter struct {
	Repo   FillRunRepo
	Logger *zap.Logger
}

func NewFillRunLifecycleAdapter(repo FillRunRepo, logger *zap.Logger) *FillRunLifecycleAdapter {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &FillRunLifecycleAdapter{Repo: repo, Logger: logger}
}

func (l *FillRunLifecycleAdapter) MarkFillRunRunning(ctx context.Context, runID uuid.UUID, jobID uuid.UUID) error {
	_ = jobID
	if l == nil || l.Repo == nil || runID == uuid.Nil {
		return nil
	}
	return l.Repo.MarkRunning(ctx, runID, time.Now().UTC())
}

func (l *FillRunLifecycleAdapter) MarkFillRunSucceeded(ctx context.Context, runID uuid.UUID, result *pythonpkg.Step15RunResult, artifacts []artifact.RunArtifact) error {
	if l == nil || l.Repo == nil || runID == uuid.Nil {
		return nil
	}
	return l.Repo.MarkSucceeded(ctx, runID, time.Now().UTC(), completionUpdate(result, artifacts))
}

func (l *FillRunLifecycleAdapter) MarkFillRunCompletedWithFailures(ctx context.Context, runID uuid.UUID, result *pythonpkg.Step15RunResult, artifacts []artifact.RunArtifact, errMsg string) error {
	if l == nil || l.Repo == nil || runID == uuid.Nil {
		return nil
	}
	return l.Repo.MarkCompletedWithFailures(ctx, runID, time.Now().UTC(), completionUpdate(result, artifacts), errMsg)
}

func (l *FillRunLifecycleAdapter) MarkFillRunFailed(ctx context.Context, runID uuid.UUID, err error) error {
	if l == nil || l.Repo == nil || runID == uuid.Nil {
		return nil
	}
	errMsg := ""
	if err != nil {
		errMsg = err.Error()
	}
	return l.Repo.MarkFailed(ctx, runID, time.Now().UTC(), errMsg)
}

func (l *FillRunLifecycleAdapter) MarkFillRunCanceled(ctx context.Context, runID uuid.UUID) error {
	if l == nil || l.Repo == nil || runID == uuid.Nil {
		return nil
	}
	return l.Repo.MarkCanceled(ctx, runID, time.Now().UTC())
}

func completionUpdate(result *pythonpkg.Step15RunResult, artifacts []artifact.RunArtifact) FillRunCompletionUpdate {
	var update FillRunCompletionUpdate
	if result == nil {
		return update
	}
	update.RunManifestPath = filepath.Join(result.OutDir, pythonpkg.RunManifestFilename)
	if result.Manifest != nil {
		counts := result.Manifest.Counts
		update.ProgressTotal = counts.TotalFields
		update.ProgressDone = counts.TotalFields
		update.AnsweredCount = counts.Answered
		update.PartialClueCount = counts.PartialClue
		update.NotFoundCount = counts.NotFound
		update.ConflictUnresolvedCount = counts.ConflictUnresolved
		update.ReviewRequiredCount = counts.ReviewRequired
		update.WritebackAllowedCount = counts.WritebackAllowed
		update.FailedCount = counts.Failed
		if path, ok := result.Manifest.ArtifactPath(artifact.TypeRunSummary); ok {
			update.SummaryPath = path
		} else if path, ok := result.Manifest.ArtifactPath(artifact.TypeSummary); ok {
			update.SummaryPath = path
		}
	}
	for _, item := range artifacts {
		if item.ArtifactType == artifact.TypeFilledForm {
			id := item.ID
			update.FilledFormArtifactID = &id
			break
		}
	}
	return update
}

func CompletedWithFailuresReason(result *pythonpkg.Step15RunResult) string {
	if result == nil || result.Manifest == nil {
		return "completed with failures"
	}
	if result.Manifest.Counts.Failed > 0 {
		return fmt.Sprintf("failed_count=%d", result.Manifest.Counts.Failed)
	}
	return fmt.Sprintf("manifest_status=%s", result.Manifest.Status)
}
