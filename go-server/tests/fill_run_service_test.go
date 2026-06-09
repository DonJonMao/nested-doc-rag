package tests

import (
	"context"
	"net/http"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFillRunServiceCreateCreatesJobAndQueues(t *testing.T) {
	workspaceID := uuid.New()
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx"}))
	fillRepo := newFakeFillRunRepo()
	jobSvc := &fakeJobUseCase{}
	cfg := *config.Default()
	cfg.Python.ProjectDir = t.TempDir()
	service := formpkg.NewFillRunService(fillRepo, formRepo, jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), cfg)

	run, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID, TargetNamespace: "target"}, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusQueued, run.Status)
	require.NotNil(t, run.JobID)
	require.Len(t, jobSvc.created, 1)
	require.Equal(t, jobs.JobTypeFillForm, jobSvc.created[0].JobType)
	require.Equal(t, jobs.ResourceTypeFillRun, jobSvc.created[0].ResourceType)
	require.Equal(t, run.ID, jobSvc.created[0].ResourceID)
	require.Equal(t, run.ID.String(), jobSvc.created[0].Payload["fill_run_id"])
	require.Equal(t, config.Default().Python.Step15DefaultRows, run.RowsSpec)
}

func TestFillRunServiceCancelCallsJobService(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	jobID := uuid.New()
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), JobID: &jobID, Status: formpkg.FillRunStatusRunning}))
	jobSvc := &fakeJobUseCase{cancel: &jobs.Job{ID: jobID, Status: jobs.JobStatusCancelRequested}}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	run, err := service.CancelFillRun(context.Background(), runID, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusCancelRequested, run.Status)
	require.Equal(t, []uuid.UUID{jobID}, jobSvc.canceled)
}

func TestFillRunServiceGetListPermissions(t *testing.T) {
	workspaceID := uuid.New()
	fillRepo := newFakeFillRunRepo()
	runID := uuid.New()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusQueued}))
	authorizer := &fakeAuthorizer{}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), &fakeJobUseCase{}, &fakeFillArtifactService{}, authorizer, nil, zap.NewNop(), *config.Default())

	_, err := service.GetFillRun(context.Background(), runID, auth.Principal{UserID: uuid.New()})
	require.NoError(t, err)
	runs, err := service.ListFillRuns(context.Background(), workspaceID, "", 50, 0, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Len(t, runs, 1)
	require.Equal(t, 2, authorizer.reads)
}

func TestFillRunServiceCreateRequiresWrite(t *testing.T) {
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), newFakeFormFileRepo(), &fakeJobUseCase{}, &fakeFillArtifactService{}, &fakeAuthorizer{writeErr: httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)}, nil, zap.NewNop(), *config.Default())

	_, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: uuid.New(), FormFileID: uuid.New(), TargetNamespace: "target"}, auth.Principal{UserID: uuid.New()})

	require.Error(t, err)
}
