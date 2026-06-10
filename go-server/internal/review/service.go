package review

import (
	"context"
	"net/http"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type WorkspaceAuthorizer interface {
	CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
	CanReviewWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
}

type FillRunGetter interface {
	GetByID(ctx context.Context, id uuid.UUID) (*form.FillRun, error)
}

type Service struct {
	repo       Repo
	runs       FillRunGetter
	authorizer WorkspaceAuthorizer
	audit      *audit.Service
	logger     *zap.Logger
}

func NewService(repo Repo, runs FillRunGetter, authorizer WorkspaceAuthorizer, auditSvc *audit.Service, logger *zap.Logger) *Service {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &Service{repo: repo, runs: runs, authorizer: authorizer, audit: auditSvc, logger: logger}
}

func (s *Service) ListByRun(ctx context.Context, runID uuid.UUID, filter ReviewFilter, actor auth.Principal) ([]ReviewItem, ReviewCounts, error) {
	run, err := s.runs.GetByID(ctx, runID)
	if err != nil {
		return nil, ReviewCounts{}, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, run.WorkspaceID, actor); err != nil {
		return nil, ReviewCounts{}, err
	}
	if err := validateFilter(filter); err != nil {
		return nil, ReviewCounts{}, err
	}
	filter.WorkspaceID = run.WorkspaceID
	items, err := s.repo.ListByRun(ctx, run.ID, filter)
	if err != nil {
		return nil, ReviewCounts{}, err
	}
	counts, err := s.repo.CountByRun(ctx, run.ID)
	if err != nil {
		return nil, ReviewCounts{}, err
	}
	return items, counts, nil
}

func (s *Service) CountByRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (ReviewCounts, error) {
	run, err := s.runs.GetByID(ctx, runID)
	if err != nil {
		return ReviewCounts{}, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, run.WorkspaceID, actor); err != nil {
		return ReviewCounts{}, err
	}
	return s.repo.CountByRun(ctx, run.ID)
}

func (s *Service) Get(ctx context.Context, itemID uuid.UUID, actor auth.Principal) (*ReviewItem, error) {
	item, err := s.repo.GetByID(ctx, itemID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, item.WorkspaceID, actor); err != nil {
		return nil, err
	}
	return item, nil
}

func (s *Service) Approve(ctx context.Context, itemID uuid.UUID, comment string, actor auth.Principal) (*ReviewItem, error) {
	item, err := s.requireReview(ctx, itemID, actor)
	if err != nil {
		return nil, err
	}
	if err := ensureStatusAllowed(item.Status, "approved", ReviewStatusPending, ReviewStatusReopened, ReviewStatusEdited); err != nil {
		return nil, err
	}
	if err := s.update(ctx, item, ReviewStatusApproved, strings.TrimSpace(comment), "", actor, "review.approved"); err != nil {
		return nil, err
	}
	return s.repo.GetByID(ctx, item.ID)
}

func (s *Service) Reject(ctx context.Context, itemID uuid.UUID, reason string, actor auth.Principal) (*ReviewItem, error) {
	item, err := s.requireReview(ctx, itemID, actor)
	if err != nil {
		return nil, err
	}
	reason = strings.TrimSpace(reason)
	if reason == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "reason is required", http.StatusBadRequest, nil, nil)
	}
	if err := ensureStatusAllowed(item.Status, "rejected", ReviewStatusPending, ReviewStatusReopened, ReviewStatusEdited); err != nil {
		return nil, err
	}
	if err := s.update(ctx, item, ReviewStatusRejected, reason, "", actor, "review.rejected"); err != nil {
		return nil, err
	}
	return s.repo.GetByID(ctx, item.ID)
}

func (s *Service) Edit(ctx context.Context, itemID uuid.UUID, editedAnswer string, comment string, actor auth.Principal) (*ReviewItem, error) {
	item, err := s.requireReview(ctx, itemID, actor)
	if err != nil {
		return nil, err
	}
	editedAnswer = strings.TrimSpace(editedAnswer)
	if editedAnswer == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "edited_answer is required", http.StatusBadRequest, nil, nil)
	}
	if err := ensureStatusAllowed(item.Status, "edited", ReviewStatusPending, ReviewStatusReopened, ReviewStatusApproved, ReviewStatusRejected); err != nil {
		return nil, err
	}
	if err := s.update(ctx, item, ReviewStatusEdited, strings.TrimSpace(comment), editedAnswer, actor, "review.edited"); err != nil {
		return nil, err
	}
	return s.repo.GetByID(ctx, item.ID)
}

func (s *Service) Ignore(ctx context.Context, itemID uuid.UUID, comment string, actor auth.Principal) (*ReviewItem, error) {
	item, err := s.requireReview(ctx, itemID, actor)
	if err != nil {
		return nil, err
	}
	if err := ensureStatusAllowed(item.Status, "ignored", ReviewStatusPending, ReviewStatusReopened); err != nil {
		return nil, err
	}
	if err := s.update(ctx, item, ReviewStatusIgnored, strings.TrimSpace(comment), "", actor, "review.ignored"); err != nil {
		return nil, err
	}
	return s.repo.GetByID(ctx, item.ID)
}

func (s *Service) Reopen(ctx context.Context, itemID uuid.UUID, comment string, actor auth.Principal) (*ReviewItem, error) {
	item, err := s.requireReview(ctx, itemID, actor)
	if err != nil {
		return nil, err
	}
	if err := ensureStatusAllowed(item.Status, "reopened", ReviewStatusApproved, ReviewStatusRejected, ReviewStatusIgnored, ReviewStatusEdited); err != nil {
		return nil, err
	}
	if err := s.update(ctx, item, ReviewStatusReopened, strings.TrimSpace(comment), "", actor, "review.reopened"); err != nil {
		return nil, err
	}
	return s.repo.GetByID(ctx, item.ID)
}

func (s *Service) requireReview(ctx context.Context, itemID uuid.UUID, actor auth.Principal) (*ReviewItem, error) {
	item, err := s.repo.GetByID(ctx, itemID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReviewWorkspace(ctx, item.WorkspaceID, actor); err != nil {
		return nil, err
	}
	return item, nil
}

func (s *Service) update(ctx context.Context, item *ReviewItem, status string, comment string, editedAnswer string, actor auth.Principal, auditAction string) error {
	now := time.Now().UTC()
	if err := s.repo.UpdateStatus(ctx, item.ID, ReviewStatusUpdate{
		Status:        status,
		ReviewerID:    actor.UserID,
		ReviewComment: comment,
		EditedAnswer:  editedAnswer,
		ReviewedAt:    now,
	}); err != nil {
		return err
	}
	s.record(ctx, actor, item.WorkspaceID, auditAction, "review_item", item.ID.String(), map[string]any{
		"run_id":   item.RunID.String(),
		"field_id": item.FieldID,
		"status":   status,
	})
	return nil
}

func (s *Service) record(ctx context.Context, actor auth.Principal, workspaceID uuid.UUID, action string, resourceType string, resourceID string, payload map[string]any) {
	if s.audit != nil {
		s.audit.Record(ctx, audit.AuditLog{WorkspaceID: &workspaceID, UserID: &actor.UserID, Action: action, ResourceType: resourceType, ResourceID: resourceID, Payload: payload})
	}
}

func validateFilter(filter ReviewFilter) error {
	if filter.Status != "" && !ValidReviewStatus(filter.Status) {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "invalid review status", http.StatusBadRequest, map[string]string{"status": filter.Status}, nil)
	}
	if filter.RiskLevel != "" && !ValidRiskLevel(filter.RiskLevel) {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "invalid risk_level", http.StatusBadRequest, map[string]string{"risk_level": filter.RiskLevel}, nil)
	}
	return nil
}

func ensureStatusAllowed(current string, action string, allowed ...string) error {
	for _, status := range allowed {
		if current == status {
			return nil
		}
	}
	return httpx.NewAppError(httpx.CodeConflict, "review item cannot be "+action+" from current status", http.StatusConflict, map[string]string{"status": current}, nil)
}
