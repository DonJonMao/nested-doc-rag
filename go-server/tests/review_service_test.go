package tests

import (
	"context"
	"net/http"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	reviewpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/review"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestReviewServiceListByRunChecksWorkspaceRead(t *testing.T) {
	service, repo, runs, authorizer, _, actor, workspaceID, runID := newReviewServiceFixture(t)
	require.NoError(t, repo.Create(context.Background(), reviewpkg.ReviewItem{ID: uuid.New(), WorkspaceID: workspaceID, RunID: runID, Status: reviewpkg.ReviewStatusPending, ReviewRequired: true}))

	items, counts, err := service.ListByRun(context.Background(), runID, reviewpkg.ReviewFilter{}, actor)

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, 1, counts.Total)
	require.Equal(t, 1, authorizer.reads)
	_, err = runs.GetByID(context.Background(), runID)
	require.NoError(t, err)
}

func TestReviewServiceGetChecksWorkspaceRead(t *testing.T) {
	service, repo, _, authorizer, _, actor, workspaceID, runID := newReviewServiceFixture(t)
	itemID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), reviewpkg.ReviewItem{ID: itemID, WorkspaceID: workspaceID, RunID: runID, Status: reviewpkg.ReviewStatusPending}))

	item, err := service.Get(context.Background(), itemID, actor)

	require.NoError(t, err)
	require.Equal(t, itemID, item.ID)
	require.Equal(t, 1, authorizer.reads)
}

func TestReviewServiceApproveChecksReviewPermissionAndAudits(t *testing.T) {
	service, repo, _, authorizer, audits, actor, workspaceID, runID := newReviewServiceFixture(t)
	itemID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), reviewpkg.ReviewItem{ID: itemID, WorkspaceID: workspaceID, RunID: runID, Status: reviewpkg.ReviewStatusPending, ReviewRequired: true}))

	item, err := service.Approve(context.Background(), itemID, "确认可用", actor)

	require.NoError(t, err)
	require.Equal(t, reviewpkg.ReviewStatusApproved, item.Status)
	require.Equal(t, "确认可用", item.ReviewComment)
	require.Equal(t, 1, authorizer.reviews)
	require.Len(t, audits.logs, 1)
	require.Equal(t, "review.approved", audits.logs[0].Action)
}

func TestReviewServiceRejectRequiresReason(t *testing.T) {
	service, repo, _, _, audits, actor, workspaceID, runID := newReviewServiceFixture(t)
	itemID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), reviewpkg.ReviewItem{ID: itemID, WorkspaceID: workspaceID, RunID: runID, Status: reviewpkg.ReviewStatusPending}))

	_, err := service.Reject(context.Background(), itemID, "", actor)

	require.Error(t, err)
	require.Equal(t, httpx.CodeInvalidArgument, httpx.ErrorFrom(err).Code)

	item, err := service.Reject(context.Background(), itemID, "证据不足", actor)
	require.NoError(t, err)
	require.Equal(t, reviewpkg.ReviewStatusRejected, item.Status)
	require.Equal(t, "证据不足", item.ReviewComment)
	require.Equal(t, "review.rejected", audits.logs[0].Action)
}

func TestReviewServiceEditRequiresEditedAnswer(t *testing.T) {
	service, repo, _, _, audits, actor, workspaceID, runID := newReviewServiceFixture(t)
	itemID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), reviewpkg.ReviewItem{ID: itemID, WorkspaceID: workspaceID, RunID: runID, Status: reviewpkg.ReviewStatusPending}))

	_, err := service.Edit(context.Background(), itemID, " ", "empty", actor)

	require.Error(t, err)
	require.Equal(t, httpx.CodeInvalidArgument, httpx.ErrorFrom(err).Code)

	item, err := service.Edit(context.Background(), itemID, "人工确认", "现场确认", actor)
	require.NoError(t, err)
	require.Equal(t, reviewpkg.ReviewStatusEdited, item.Status)
	require.Equal(t, "人工确认", item.EditedAnswer)
	require.Equal(t, "review.edited", audits.logs[0].Action)
}

func TestReviewServiceIgnoreAndReopen(t *testing.T) {
	service, repo, _, _, audits, actor, workspaceID, runID := newReviewServiceFixture(t)
	itemID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), reviewpkg.ReviewItem{ID: itemID, WorkspaceID: workspaceID, RunID: runID, Status: reviewpkg.ReviewStatusPending}))

	ignored, err := service.Ignore(context.Background(), itemID, "无需处理", actor)
	require.NoError(t, err)
	require.Equal(t, reviewpkg.ReviewStatusIgnored, ignored.Status)
	require.Equal(t, "review.ignored", audits.logs[0].Action)

	reopened, err := service.Reopen(context.Background(), itemID, "重新审核", actor)
	require.NoError(t, err)
	require.Equal(t, reviewpkg.ReviewStatusReopened, reopened.Status)
	require.Equal(t, "review.reopened", audits.logs[1].Action)
	require.Len(t, audits.logs, 2)
}

func TestReviewServiceViewerCannotApprove(t *testing.T) {
	service, repo, _, authorizer, _, actor, workspaceID, runID := newReviewServiceFixture(t)
	authorizer.reviewErr = httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)
	itemID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), reviewpkg.ReviewItem{ID: itemID, WorkspaceID: workspaceID, RunID: runID, Status: reviewpkg.ReviewStatusPending}))

	_, err := service.Approve(context.Background(), itemID, "no", actor)

	require.Error(t, err)
	require.Equal(t, httpx.CodeForbidden, httpx.ErrorFrom(err).Code)
	require.Zero(t, repo.updates)
}

func TestReviewServiceInvalidCurrentStatusConflict(t *testing.T) {
	service, repo, _, _, _, actor, workspaceID, runID := newReviewServiceFixture(t)
	itemID := uuid.New()
	require.NoError(t, repo.Create(context.Background(), reviewpkg.ReviewItem{ID: itemID, WorkspaceID: workspaceID, RunID: runID, Status: reviewpkg.ReviewStatusApproved}))

	_, err := service.Approve(context.Background(), itemID, "重复确认", actor)

	require.Error(t, err)
	require.Equal(t, httpx.CodeConflict, httpx.ErrorFrom(err).Code)
	require.Zero(t, repo.updates)
}

func newReviewServiceFixture(t *testing.T) (*reviewpkg.Service, *fakeReviewRepo, *fakeFillRunRepo, *fakeReviewAuthorizer, *fakeAuditRepo, auth.Principal, uuid.UUID, uuid.UUID) {
	t.Helper()
	repo := newFakeReviewRepo()
	runs := newFakeFillRunRepo()
	workspaceID := uuid.New()
	runID := uuid.New()
	require.NoError(t, runs.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, Status: formpkg.FillRunStatusSucceeded}))
	authorizer := &fakeReviewAuthorizer{}
	audits := &fakeAuditRepo{}
	service := reviewpkg.NewService(repo, runs, authorizer, audit.NewService(audits, zap.NewNop()), zap.NewNop())
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleReviewer}}
	return service, repo, runs, authorizer, audits, actor, workspaceID, runID
}
