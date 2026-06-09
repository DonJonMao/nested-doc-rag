package tests

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/eventbus"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFakeEventBusPublishSubscribe(t *testing.T) {
	bus := eventbus.NewFakeEventBus()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	received := make(chan runevent.RunEvent, 1)
	go func() {
		_ = bus.Subscribe(ctx, func(event runevent.RunEvent) {
			received <- event
		})
	}()
	require.Eventually(t, func() bool { return bus.SubscriberCount() == 1 }, time.Second, 10*time.Millisecond)
	event := runevent.RunEvent{ID: uuid.New(), WorkspaceID: uuid.New(), RunID: uuid.New(), EventType: runevent.EventProgress}

	require.NoError(t, bus.Publish(context.Background(), event))

	select {
	case got := <-received:
		require.Equal(t, event.ID, got.ID)
		require.Equal(t, event.RunID, got.RunID)
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for fake event bus event")
	}
}

func TestRunEventPublisherPublishesToBus(t *testing.T) {
	bus := eventbus.NewFakeEventBus()
	publisher := eventbus.NewRunEventPublisher(bus, zap.NewNop())
	event := runevent.RunEvent{ID: uuid.New(), WorkspaceID: uuid.New(), RunID: uuid.New(), EventType: runevent.EventSucceeded}

	publisher.PublishRunEvent(event)

	published := bus.Published()
	require.Len(t, published, 1)
	require.Equal(t, event.ID, published[0].ID)
}

func TestRunEventPublisherDoesNotPanicOnPublishError(t *testing.T) {
	bus := eventbus.NewFakeEventBus()
	bus.PublishErr = errors.New("redis unavailable")
	publisher := eventbus.NewRunEventPublisher(bus, zap.NewNop())

	require.NotPanics(t, func() {
		publisher.PublishRunEvent(runevent.RunEvent{ID: uuid.New(), RunID: uuid.New(), EventType: runevent.EventFailed})
	})
}
