package sse

import (
	"sync"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
)

type Broker struct {
	mu         sync.RWMutex
	bufferSize int
	closed     bool
	subs       map[uuid.UUID]map[chan Event]struct{}
}

func NewBroker(bufferSize int) *Broker {
	if bufferSize <= 0 {
		bufferSize = 256
	}
	return &Broker{bufferSize: bufferSize, subs: make(map[uuid.UUID]map[chan Event]struct{})}
}

func (b *Broker) Subscribe(runID uuid.UUID) (<-chan Event, func()) {
	ch := make(chan Event, b.bufferSize)
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.closed {
		close(ch)
		return ch, func() {}
	}
	if b.subs[runID] == nil {
		b.subs[runID] = make(map[chan Event]struct{})
	}
	b.subs[runID][ch] = struct{}{}
	return ch, func() {
		b.mu.Lock()
		defer b.mu.Unlock()
		if _, ok := b.subs[runID][ch]; ok {
			delete(b.subs[runID], ch)
			close(ch)
		}
		if len(b.subs[runID]) == 0 {
			delete(b.subs, runID)
		}
	}
}

func (b *Broker) Publish(event Event) {
	b.mu.RLock()
	defer b.mu.RUnlock()
	if b.closed {
		return
	}
	for ch := range b.subs[event.RunID] {
		select {
		case ch <- event:
		default:
		}
	}
}

func (b *Broker) PublishRunEvent(event runevent.RunEvent) {
	b.Publish(Event{
		RunID:     event.RunID,
		EventType: event.EventType,
		Sequence:  event.Sequence,
		Payload:   event.Payload,
		CreatedAt: event.CreatedAt,
	})
}

func (b *Broker) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.closed {
		return
	}
	b.closed = true
	for runID, subscribers := range b.subs {
		for ch := range subscribers {
			close(ch)
		}
		delete(b.subs, runID)
	}
}
