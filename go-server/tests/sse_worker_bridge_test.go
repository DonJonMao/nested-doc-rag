package tests

import (
	"context"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/eventbus"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/sse"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestWorkerEventBusBridgePublishesToAPIBroker(t *testing.T) {
	bus := eventbus.NewFakeEventBus()
	broker := sse.NewBroker(8)
	defer broker.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		_ = bus.Subscribe(ctx, func(event runevent.RunEvent) {
			broker.PublishRunEvent(event)
		})
	}()
	require.Eventually(t, func() bool { return bus.SubscriberCount() == 1 }, time.Second, 10*time.Millisecond)
	workspaceID := uuid.New()
	runID := uuid.New()
	events, unsubscribe := broker.Subscribe(runID)
	defer unsubscribe()
	workerEvents := runevent.NewService(&fakeRunEventRepo{}, eventbus.NewRunEventPublisher(bus, zap.NewNop()))

	created, err := workerEvents.Create(context.Background(), runevent.RunEvent{WorkspaceID: workspaceID, RunID: runID, EventType: runevent.EventProgress, Payload: map[string]any{"percent": 50}})

	require.NoError(t, err)
	select {
	case got := <-events:
		require.Equal(t, created.RunID, got.RunID)
		require.Equal(t, created.EventType, got.EventType)
		require.Equal(t, created.Sequence, got.Sequence)
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for bridged SSE event")
	}
}
