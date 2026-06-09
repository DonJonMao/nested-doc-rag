package eventbus

import (
	"context"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"go.uber.org/zap"
)

type RunEventPublisher struct {
	bus    EventBus
	logger *zap.Logger
}

func NewRunEventPublisher(bus EventBus, logger *zap.Logger) *RunEventPublisher {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &RunEventPublisher{bus: bus, logger: logger}
}

func (p *RunEventPublisher) PublishRunEvent(event runevent.RunEvent) {
	if p == nil || p.bus == nil {
		return
	}
	if err := p.bus.Publish(context.Background(), event); err != nil {
		p.logger.Error("publish run event failed", zap.String("event_id", event.ID.String()), zap.String("run_id", event.RunID.String()), zap.String("event_type", event.EventType), zap.Error(err))
	}
}
