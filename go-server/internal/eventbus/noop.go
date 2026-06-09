package eventbus

import (
	"context"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
)

type NoopEventBus struct{}

func NewNoopEventBus() *NoopEventBus {
	return &NoopEventBus{}
}

func (b *NoopEventBus) Publish(ctx context.Context, event runevent.RunEvent) error {
	return nil
}

func (b *NoopEventBus) Subscribe(ctx context.Context, handler func(runevent.RunEvent)) error {
	<-ctx.Done()
	return nil
}

func (b *NoopEventBus) Close() error {
	return nil
}
