package audit

import (
	"context"
	"strings"

	"github.com/google/uuid"
	"go.uber.org/zap"
)

type Service struct {
	repo   Repo
	logger *zap.Logger
}

func NewService(repo Repo, logger *zap.Logger) *Service {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &Service{repo: repo, logger: logger}
}

func (s *Service) Record(ctx context.Context, log AuditLog) {
	if s == nil || s.repo == nil {
		return
	}
	if log.ID == uuid.Nil {
		log.ID = uuid.New()
	}
	log.Payload = SanitizePayload(log.Payload)
	if err := s.repo.Create(ctx, log); err != nil {
		s.logger.Error("audit log write failed", zap.String("action", log.Action), zap.Error(err))
	}
}

func SanitizePayload(payload map[string]any) map[string]any {
	if payload == nil {
		return nil
	}
	clean := make(map[string]any, len(payload))
	for key, value := range payload {
		lower := strings.ToLower(key)
		if strings.Contains(lower, "password") ||
			strings.Contains(lower, "token") ||
			strings.Contains(lower, "authorization") ||
			strings.Contains(lower, "secret") {
			continue
		}
		clean[key] = value
	}
	return clean
}
