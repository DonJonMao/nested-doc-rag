package tests

import (
	"context"
	"testing"
	"time"

	reviewpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/review"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestReviewRepoContractUpsertCountStatusAndDelete(t *testing.T) {
	repo := newFakeReviewRepo()
	runID := uuid.New()
	workspaceID := uuid.New()
	item := reviewpkg.ReviewItem{
		ID:               uuid.New(),
		WorkspaceID:      workspaceID,
		RunID:            runID,
		FieldID:          "f1",
		Status:           reviewpkg.ReviewStatusPending,
		RiskLevel:        reviewpkg.ReviewRiskHigh,
		ReviewRequired:   true,
		WritebackAllowed: true,
	}

	require.NoError(t, repo.UpsertByRunAndField(context.Background(), item))
	item.AnswerValue = "updated"
	require.NoError(t, repo.UpsertByRunAndField(context.Background(), item))

	counts, err := repo.CountByRun(context.Background(), runID)
	require.NoError(t, err)
	require.Equal(t, 1, counts.Total)
	require.Equal(t, 1, counts.Pending)
	require.Equal(t, 1, counts.HighRisk)
	require.Equal(t, 1, counts.WritebackAllowed)

	require.NoError(t, repo.UpdateStatus(context.Background(), item.ID, reviewpkg.ReviewStatusUpdate{
		Status:        reviewpkg.ReviewStatusApproved,
		ReviewerID:    uuid.New(),
		ReviewComment: "ok",
		ReviewedAt:    time.Now().UTC(),
	}))
	updated, err := repo.GetByID(context.Background(), item.ID)
	require.NoError(t, err)
	require.Equal(t, reviewpkg.ReviewStatusApproved, updated.Status)

	require.NoError(t, repo.DeleteByRun(context.Background(), runID))
	counts, err = repo.CountByRun(context.Background(), runID)
	require.NoError(t, err)
	require.Zero(t, counts.Total)
}
