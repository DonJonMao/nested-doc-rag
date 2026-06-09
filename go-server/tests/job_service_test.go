package tests

import (
	"context"
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

func TestJobServiceCreateJobChecksWorkspaceWriteAndQueues(t *testing.T) {
	repo := newFakeJobRepo()
	queue := &fakeQueue{}
	eventRepo := &fakeRunEventRepo{}
	auditRepo := &fakeAuditRepo{}
	authorizer := &fakeAuthorizer{}
	service := jobs.NewService(repo, runevent.NewService(eventRepo, nil), queue, authorizer, audit.NewService(auditRepo, zap.NewNop()), zap.NewNop(), 3)
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	workspaceID := uuid.New()

	job, err := service.CreateJob(context.Background(), jobs.CreateJobRequest{
		WorkspaceID:  workspaceID,
		JobType:      jobs.JobTypeNoop,
		ResourceType: jobs.ResourceTypeNoop,
		Payload:      map[string]any{"sleep_ms": 1},
	}, actor)

	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusQueued, job.Status)
	require.Equal(t, 2, authorizer.writes)
	require.Len(t, queue.enqueued, 1)
	require.Len(t, eventRepo.events, 1)
	require.Equal(t, runevent.EventQueued, eventRepo.events[0].EventType)
	require.Len(t, auditRepo.logs, 2)
}

func TestJobServiceCreateJobWriteForbidden(t *testing.T) {
	service := jobs.NewService(newFakeJobRepo(), nil, &fakeQueue{}, &fakeAuthorizer{writeErr: httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)}, nil, zap.NewNop(), 3)

	_, err := service.CreateJob(context.Background(), jobs.CreateJobRequest{WorkspaceID: uuid.New(), JobType: jobs.JobTypeNoop, ResourceType: jobs.ResourceTypeNoop}, auth.Principal{UserID: uuid.New()})

	requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
}

func TestJobServiceCancelQueuedAndRunning(t *testing.T) {
	repo := newFakeJobRepo()
	eventRepo := &fakeRunEventRepo{}
	service := jobs.NewService(repo, runevent.NewService(eventRepo, nil), &fakeQueue{}, &fakeAuthorizer{}, nil, zap.NewNop(), 3)
	actor := auth.Principal{UserID: uuid.New()}
	workspaceID := uuid.New()
	queued := jobs.Job{ID: uuid.New(), WorkspaceID: workspaceID, JobType: jobs.JobTypeNoop, ResourceType: jobs.ResourceTypeNoop, ResourceID: uuid.New(), Status: jobs.JobStatusQueued, MaxAttempts: 3}
	running := jobs.Job{ID: uuid.New(), WorkspaceID: workspaceID, JobType: jobs.JobTypeNoop, ResourceType: jobs.ResourceTypeNoop, ResourceID: uuid.New(), Status: jobs.JobStatusRunning, MaxAttempts: 3}
	repo.add(queued)
	repo.add(running)

	canceled, err := service.CancelJob(context.Background(), queued.ID, actor)
	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusCanceled, canceled.Status)

	cancelRequested, err := service.CancelJob(context.Background(), running.ID, actor)
	require.NoError(t, err)
	require.Equal(t, jobs.JobStatusCancelRequested, cancelRequested.Status)
	require.Len(t, eventRepo.events, 2)
}

func TestJobServiceGetAndListCheckWorkspaceRead(t *testing.T) {
	repo := newFakeJobRepo()
	authorizer := &fakeAuthorizer{}
	service := jobs.NewService(repo, nil, nil, authorizer, nil, zap.NewNop(), 3)
	actor := auth.Principal{UserID: uuid.New()}
	workspaceID := uuid.New()
	job := jobs.Job{ID: uuid.New(), WorkspaceID: workspaceID, JobType: jobs.JobTypeNoop, ResourceType: jobs.ResourceTypeNoop, ResourceID: uuid.New(), Status: jobs.JobStatusQueued, MaxAttempts: 3}
	repo.add(job)

	got, err := service.GetJob(context.Background(), job.ID, actor)
	require.NoError(t, err)
	require.Equal(t, job.ID, got.ID)
	listed, err := service.ListJobs(context.Background(), workspaceID, "", 50, 0, actor)
	require.NoError(t, err)
	require.Len(t, listed, 1)
	require.Equal(t, 2, authorizer.reads)
}
