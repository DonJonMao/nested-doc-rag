package tests

import (
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	reviewpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/review"
	"github.com/google/uuid"
)

type fakeReviewRepo struct {
	mu      sync.Mutex
	items   map[uuid.UUID]reviewpkg.ReviewItem
	upserts int
	updates int
}

func newFakeReviewRepo() *fakeReviewRepo {
	return &fakeReviewRepo{items: make(map[uuid.UUID]reviewpkg.ReviewItem)}
}

func (f *fakeReviewRepo) Create(ctx context.Context, item reviewpkg.ReviewItem) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if item.ID == uuid.Nil {
		item.ID = uuid.New()
	}
	if item.Status == "" {
		item.Status = reviewpkg.ReviewStatusPending
	}
	f.items[item.ID] = item
	return nil
}

func (f *fakeReviewRepo) UpsertByRunAndField(ctx context.Context, item reviewpkg.ReviewItem) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.upserts++
	for id, existing := range f.items {
		if sameReviewIdentity(existing, item) {
			item.ID = id
			if existing.Status != "" && existing.Status != reviewpkg.ReviewStatusPending {
				item.Status = existing.Status
				item.ReviewerID = existing.ReviewerID
				item.ReviewedAt = existing.ReviewedAt
				item.ReviewComment = existing.ReviewComment
				item.EditedAnswer = existing.EditedAnswer
			}
			f.items[id] = item
			return nil
		}
	}
	if item.ID == uuid.Nil {
		item.ID = uuid.New()
	}
	if item.Status == "" {
		item.Status = reviewpkg.ReviewStatusPending
	}
	f.items[item.ID] = item
	return nil
}

func (f *fakeReviewRepo) GetByID(ctx context.Context, id uuid.UUID) (*reviewpkg.ReviewItem, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	item, ok := f.items[id]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "review item not found", http.StatusNotFound, nil, nil)
	}
	return &item, nil
}

func (f *fakeReviewRepo) ListByRun(ctx context.Context, runID uuid.UUID, filter reviewpkg.ReviewFilter) ([]reviewpkg.ReviewItem, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []reviewpkg.ReviewItem
	for _, item := range f.items {
		if item.RunID != runID {
			continue
		}
		if filter.WorkspaceID != uuid.Nil && item.WorkspaceID != filter.WorkspaceID {
			continue
		}
		if filter.Status != "" && item.Status != filter.Status {
			continue
		}
		if filter.RiskLevel != "" && item.RiskLevel != filter.RiskLevel {
			continue
		}
		if filter.ReviewRequired != nil && item.ReviewRequired != *filter.ReviewRequired {
			continue
		}
		if filter.WritebackAllowed != nil && item.WritebackAllowed != *filter.WritebackAllowed {
			continue
		}
		out = append(out, item)
	}
	return out, nil
}

func (f *fakeReviewRepo) CountByRun(ctx context.Context, runID uuid.UUID) (reviewpkg.ReviewCounts, error) {
	items, _ := f.ListByRun(ctx, runID, reviewpkg.ReviewFilter{})
	var counts reviewpkg.ReviewCounts
	for _, item := range items {
		counts.Total++
		switch item.Status {
		case reviewpkg.ReviewStatusPending:
			counts.Pending++
		case reviewpkg.ReviewStatusApproved:
			counts.Approved++
		case reviewpkg.ReviewStatusRejected:
			counts.Rejected++
		case reviewpkg.ReviewStatusEdited:
			counts.Edited++
		case reviewpkg.ReviewStatusIgnored:
			counts.Ignored++
		case reviewpkg.ReviewStatusReopened:
			counts.Reopened++
		}
		if item.RiskLevel == reviewpkg.ReviewRiskHigh {
			counts.HighRisk++
		}
		if item.ReviewRequired {
			counts.ReviewRequired++
		}
		if item.WritebackAllowed {
			counts.WritebackAllowed++
		}
	}
	return counts, nil
}

func (f *fakeReviewRepo) UpdateStatus(ctx context.Context, id uuid.UUID, update reviewpkg.ReviewStatusUpdate) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.updates++
	item, ok := f.items[id]
	if !ok {
		return httpx.NewAppError(httpx.CodeNotFound, "review item not found", http.StatusNotFound, nil, nil)
	}
	item.Status = update.Status
	item.ReviewerID = &update.ReviewerID
	item.ReviewedAt = &update.ReviewedAt
	item.ReviewComment = update.ReviewComment
	item.EditedAnswer = update.EditedAnswer
	item.UpdatedAt = time.Now().UTC()
	f.items[id] = item
	return nil
}

func (f *fakeReviewRepo) DeleteByRun(ctx context.Context, runID uuid.UUID) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	for id, item := range f.items {
		if item.RunID == runID {
			delete(f.items, id)
		}
	}
	return nil
}

func sameReviewIdentity(a reviewpkg.ReviewItem, b reviewpkg.ReviewItem) bool {
	if a.RunID != b.RunID {
		return false
	}
	if a.FieldID != "" && b.FieldID != "" {
		return a.FieldID == b.FieldID
	}
	return a.RowIndex == b.RowIndex && a.TargetCell == b.TargetCell
}

type fakeReviewAuthorizer struct {
	readErr   error
	reviewErr error
	reads     int
	reviews   int
}

func (f *fakeReviewAuthorizer) CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	f.reads++
	return f.readErr
}

func (f *fakeReviewAuthorizer) CanReviewWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error {
	f.reviews++
	return f.reviewErr
}

type fakeReviewUseCase struct {
	items  []reviewpkg.ReviewItem
	counts reviewpkg.ReviewCounts
	item   *reviewpkg.ReviewItem
	err    error

	listRunIDs []uuid.UUID
	actions    []string
}

func (f *fakeReviewUseCase) ListByRun(ctx context.Context, runID uuid.UUID, filter reviewpkg.ReviewFilter, actor auth.Principal) ([]reviewpkg.ReviewItem, reviewpkg.ReviewCounts, error) {
	f.listRunIDs = append(f.listRunIDs, runID)
	if f.err != nil {
		return nil, reviewpkg.ReviewCounts{}, f.err
	}
	return f.items, f.counts, nil
}

func (f *fakeReviewUseCase) CountByRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (reviewpkg.ReviewCounts, error) {
	if f.err != nil {
		return reviewpkg.ReviewCounts{}, f.err
	}
	return f.counts, nil
}

func (f *fakeReviewUseCase) Get(ctx context.Context, itemID uuid.UUID, actor auth.Principal) (*reviewpkg.ReviewItem, error) {
	if f.err != nil {
		return nil, f.err
	}
	return f.item, nil
}

func (f *fakeReviewUseCase) Approve(ctx context.Context, itemID uuid.UUID, comment string, actor auth.Principal) (*reviewpkg.ReviewItem, error) {
	return f.action(reviewpkg.ReviewActionApprove)
}

func (f *fakeReviewUseCase) Reject(ctx context.Context, itemID uuid.UUID, reason string, actor auth.Principal) (*reviewpkg.ReviewItem, error) {
	return f.action(reviewpkg.ReviewActionReject)
}

func (f *fakeReviewUseCase) Edit(ctx context.Context, itemID uuid.UUID, editedAnswer string, comment string, actor auth.Principal) (*reviewpkg.ReviewItem, error) {
	return f.action(reviewpkg.ReviewActionEdit)
}

func (f *fakeReviewUseCase) Ignore(ctx context.Context, itemID uuid.UUID, comment string, actor auth.Principal) (*reviewpkg.ReviewItem, error) {
	return f.action(reviewpkg.ReviewActionIgnore)
}

func (f *fakeReviewUseCase) Reopen(ctx context.Context, itemID uuid.UUID, comment string, actor auth.Principal) (*reviewpkg.ReviewItem, error) {
	return f.action(reviewpkg.ReviewActionReopen)
}

func (f *fakeReviewUseCase) action(action string) (*reviewpkg.ReviewItem, error) {
	f.actions = append(f.actions, action)
	if f.err != nil {
		return nil, f.err
	}
	return f.item, nil
}

type fakeReviewResultRuns struct {
	run       *formpkg.FillRun
	artifacts []artifact.RunArtifact
	err       error
}

func (f *fakeReviewResultRuns) GetFillRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*formpkg.FillRun, error) {
	if f.err != nil {
		return nil, f.err
	}
	return f.run, nil
}

func (f *fakeReviewResultRuns) GetFillRunArtifacts(ctx context.Context, runID uuid.UUID, actor auth.Principal) ([]artifact.RunArtifact, error) {
	if f.err != nil {
		return nil, f.err
	}
	return f.artifacts, nil
}

type recordingReviewImporter struct {
	result reviewImportResultCompat
	err    error
	calls  []uuid.UUID
}

type reviewImportResultCompat struct {
	TotalParsed      int
	Created          int
	Updated          int
	ParseErrors      int
	ReviewRequired   int
	WritebackAllowed int
}

func (r *recordingReviewImporter) ImportForFillRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, manifest *pythonpkg.RunManifest) (jobs.ReviewImportResult, error) {
	r.calls = append(r.calls, runID)
	return jobs.ReviewImportResult{
		TotalParsed:      r.result.TotalParsed,
		Created:          r.result.Created,
		Updated:          r.result.Updated,
		ParseErrors:      r.result.ParseErrors,
		ReviewRequired:   r.result.ReviewRequired,
		WritebackAllowed: r.result.WritebackAllowed,
	}, r.err
}
