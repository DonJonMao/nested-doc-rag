package tests

import (
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/sse"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestSSEBrokerSubscribePublishAndUnsubscribe(t *testing.T) {
	broker := sse.NewBroker(2)
	runID := uuid.New()
	ch, unsubscribe := broker.Subscribe(runID)
	event := sse.Event{RunID: runID, EventType: "progress", Sequence: 1}

	broker.Publish(event)

	require.Equal(t, event, receiveSSE(t, ch))
	unsubscribe()
	broker.Publish(sse.Event{RunID: runID, EventType: "progress", Sequence: 2})
	_, ok := <-ch
	require.False(t, ok)
}

func TestSSEBrokerMultipleAndSlowSubscribers(t *testing.T) {
	broker := sse.NewBroker(1)
	runID := uuid.New()
	fast, fastUnsub := broker.Subscribe(runID)
	defer fastUnsub()
	slow, slowUnsub := broker.Subscribe(runID)
	defer slowUnsub()

	broker.Publish(sse.Event{RunID: runID, EventType: "progress", Sequence: 1})
	broker.Publish(sse.Event{RunID: runID, EventType: "progress", Sequence: 2})

	require.Equal(t, int64(1), receiveSSE(t, fast).Sequence)
	require.Equal(t, int64(1), receiveSSE(t, slow).Sequence)
}

func receiveSSE(t *testing.T, ch <-chan sse.Event) sse.Event {
	t.Helper()
	select {
	case event := <-ch:
		return event
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for SSE event")
	}
	return sse.Event{}
}
