package eventbus

import (
	"context"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
)

type EventBus interface {
	Publish(ctx context.Context, event runevent.RunEvent) error
	Subscribe(ctx context.Context, handler func(runevent.RunEvent)) error
	Close() error
}
