package runevent

import (
	"context"
	"net/http"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
)

type Publisher interface {
	PublishRunEvent(event RunEvent)
}

type Service struct {
	repo      Repo
	publisher Publisher
}

func NewService(repo Repo, publisher Publisher) *Service {
	return &Service{repo: repo, publisher: publisher}
}

func (s *Service) Create(ctx context.Context, event RunEvent) (*RunEvent, error) {
	if strings.TrimSpace(event.EventType) == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "event_type is required", http.StatusBadRequest, nil, nil)
	}
	created, err := s.repo.Create(ctx, event)
	if err != nil {
		return nil, err
	}
	if s.publisher != nil {
		s.publisher.PublishRunEvent(*created)
	}
	return created, nil
}

func (s *Service) ListByRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, afterSequence int64, limit int) ([]RunEvent, error) {
	return s.repo.ListByRun(ctx, workspaceID, runID, afterSequence, limit)
}

func (s *Service) LastSequence(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID) (int64, error) {
	return s.repo.LastSequence(ctx, workspaceID, runID)
}
