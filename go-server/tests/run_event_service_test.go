package tests

import (
	"context"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestRunEventServiceCreateListAndLastSequence(t *testing.T) {
	repo := &fakeRunEventRepo{}
	service := runevent.NewService(repo, nil)
	workspaceID := uuid.New()
	runID := uuid.New()

	first, err := service.Create(context.Background(), runevent.RunEvent{WorkspaceID: workspaceID, RunID: runID, EventType: runevent.EventQueued, Payload: map[string]any{"step": 1}})
	require.NoError(t, err)
	second, err := service.Create(context.Background(), runevent.RunEvent{WorkspaceID: workspaceID, RunID: runID, EventType: runevent.EventRunning})
	require.NoError(t, err)

	events, err := service.ListByRun(context.Background(), workspaceID, runID, first.Sequence, 10)
	require.NoError(t, err)
	require.Len(t, events, 1)
	require.Equal(t, second.Sequence, events[0].Sequence)
	last, err := service.LastSequence(context.Background(), workspaceID, runID)
	require.NoError(t, err)
	require.Equal(t, second.Sequence, last)
}
