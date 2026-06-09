package eventbus

import (
	"context"
	"sync"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
)

type FakeEventBus struct {
	mu          sync.Mutex
	published   []runevent.RunEvent
	subscribers []chan runevent.RunEvent
	PublishErr  error
}

func NewFakeEventBus() *FakeEventBus {
	return &FakeEventBus{}
}

func (b *FakeEventBus) Publish(ctx context.Context, event runevent.RunEvent) error {
	if b.PublishErr != nil {
		return b.PublishErr
	}
	b.mu.Lock()
	b.published = append(b.published, event)
	subscribers := append([]chan runevent.RunEvent(nil), b.subscribers...)
	b.mu.Unlock()
	for _, ch := range subscribers {
		select {
		case ch <- event:
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
	}
	return nil
}

func (b *FakeEventBus) Subscribe(ctx context.Context, handler func(runevent.RunEvent)) error {
	ch := make(chan runevent.RunEvent, 64)
	b.mu.Lock()
	b.subscribers = append(b.subscribers, ch)
	b.mu.Unlock()
	defer func() {
		b.mu.Lock()
		for i, candidate := range b.subscribers {
			if candidate == ch {
				b.subscribers = append(b.subscribers[:i], b.subscribers[i+1:]...)
				break
			}
		}
		b.mu.Unlock()
		close(ch)
	}()
	for {
		select {
		case <-ctx.Done():
			return nil
		case event := <-ch:
			handler(event)
		}
	}
}

func (b *FakeEventBus) Published() []runevent.RunEvent {
	b.mu.Lock()
	defer b.mu.Unlock()
	return append([]runevent.RunEvent(nil), b.published...)
}

func (b *FakeEventBus) SubscriberCount() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return len(b.subscribers)
}

func (b *FakeEventBus) Close() error {
	return nil
}
