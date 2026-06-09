package tests

import (
	"context"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestRunEventServiceCreateCallsPublisher(t *testing.T) {
	repo := &fakeRunEventRepo{}
	publisher := &recordingRunEventPublisher{}
	service := runevent.NewService(repo, publisher)

	created, err := service.Create(context.Background(), runevent.RunEvent{WorkspaceID: uuid.New(), RunID: uuid.New(), EventType: runevent.EventQueued})

	require.NoError(t, err)
	require.Len(t, publisher.events, 1)
	require.Equal(t, created.ID, publisher.events[0].ID)
}

func TestCompositePublisherPanicDoesNotBlockOtherPublishers(t *testing.T) {
	repo := &fakeRunEventRepo{}
	recorder := &recordingRunEventPublisher{}
	service := runevent.NewService(repo, runevent.NewCompositePublisher(panicRunEventPublisher{}, recorder))

	_, err := service.Create(context.Background(), runevent.RunEvent{WorkspaceID: uuid.New(), RunID: uuid.New(), EventType: runevent.EventRunning})

	require.NoError(t, err)
	require.Len(t, recorder.events, 1)
}

type recordingRunEventPublisher struct {
	events []runevent.RunEvent
}

func (p *recordingRunEventPublisher) PublishRunEvent(event runevent.RunEvent) {
	p.events = append(p.events, event)
}

type panicRunEventPublisher struct{}

func (panicRunEventPublisher) PublishRunEvent(event runevent.RunEvent) {
	panic("publisher failed")
}
