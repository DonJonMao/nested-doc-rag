package tests

import (
	"context"
	"errors"
	"net/http"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestEnqueueFailureMarksJobFailed(t *testing.T) {
	repo := newFakeJobRepo()
	eventRepo := &fakeRunEventRepo{}
	auditRepo := &fakeAuditRepo{}
	queue := &fakeQueue{err: errors.New("redis enqueue failed")}
	service := jobs.NewService(repo, runevent.NewService(eventRepo, nil), queue, &fakeAuthorizer{}, audit.NewService(auditRepo, zap.NewNop()), zap.NewNop(), 3)
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	job := jobs.Job{
		ID:           uuid.New(),
		WorkspaceID:  uuid.New(),
		JobType:      jobs.JobTypeNoop,
		ResourceType: jobs.ResourceTypeNoop,
		ResourceID:   uuid.New(),
		Status:       jobs.JobStatusCreated,
		MaxAttempts:  3,
		Payload:      map[string]any{},
	}
	repo.add(job)

	err := service.EnqueueJob(context.Background(), job.ID, actor)

	requireAppError(t, err, httpx.CodeInternal, http.StatusInternalServerError)
	updated, getErr := repo.GetByID(context.Background(), job.ID)
	require.NoError(t, getErr)
	require.Equal(t, jobs.JobStatusFailed, updated.Status)
	require.Contains(t, updated.ErrorMessage, "redis enqueue failed")
	require.Len(t, eventRepo.events, 1)
	require.Equal(t, runevent.EventFailed, eventRepo.events[0].EventType)
	require.Equal(t, true, eventRepo.events[0].Payload["enqueue_failed"])
	require.Len(t, auditRepo.logs, 1)
	require.Equal(t, "job.enqueue_failed", auditRepo.logs[0].Action)
}
