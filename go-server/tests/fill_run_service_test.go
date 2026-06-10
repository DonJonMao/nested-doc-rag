package tests

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
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
	audits := &fakeAuditRepo{}
	cfg := *config.Default()
	cfg.Python.ProjectDir = t.TempDir()
	service := formpkg.NewFillRunService(fillRepo, formRepo, jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, audit.NewService(audits, zap.NewNop()), zap.NewNop(), cfg)

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
	require.Equal(t, config.Default().Python.Step15DefaultRetrievalMode, run.RetrievalMode)
	require.Equal(t, config.Default().Python.Step15DefaultPromptVersion, run.PromptVersion)
	require.Len(t, audits.logs, 1)
	require.Equal(t, "fill_run.created", audits.logs[0].Action)
}

func TestFillRunServiceCancelCallsJobService(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	jobID := uuid.New()
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), JobID: &jobID, Status: formpkg.FillRunStatusRunning}))
	jobSvc := &fakeJobUseCase{cancel: &jobs.Job{ID: jobID, Status: jobs.JobStatusCancelRequested}}
	audits := &fakeAuditRepo{}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, audit.NewService(audits, zap.NewNop()), zap.NewNop(), *config.Default())

	run, err := service.CancelFillRun(context.Background(), runID, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusCancelRequested, run.Status)
	require.Equal(t, []uuid.UUID{jobID}, jobSvc.canceled)
	require.Len(t, audits.logs, 1)
	require.Equal(t, "fill_run.cancel_requested", audits.logs[0].Action)
}

func TestFillRunServiceCancelMarksCanceledWhenJobCanceled(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	jobID := uuid.New()
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), JobID: &jobID, Status: formpkg.FillRunStatusCancelRequested}))
	jobSvc := &fakeJobUseCase{cancel: &jobs.Job{ID: jobID, Status: jobs.JobStatusCanceled}}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	run, err := service.CancelFillRun(context.Background(), runID, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusCanceled, run.Status)
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

func TestFillRunServiceCreateRejectsFormWorkspaceMismatch(t *testing.T) {
	requestWorkspaceID := uuid.New()
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: uuid.New(), FileID: uuid.New(), Filename: "form.xlsx"}))
	jobSvc := &fakeJobUseCase{}
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), formRepo, jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: requestWorkspaceID, FormFileID: formID, TargetNamespace: "target"}, auth.Principal{UserID: uuid.New()})

	require.Error(t, err)
	require.Equal(t, httpx.CodeForbidden, httpx.ErrorFrom(err).Code)
	require.Empty(t, jobSvc.created)
}

func TestFillRunServiceCreateRequiresTargetNamespace(t *testing.T) {
	workspaceID := uuid.New()
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx"}))
	jobSvc := &fakeJobUseCase{}
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), formRepo, jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID}, auth.Principal{UserID: uuid.New()})

	require.Error(t, err)
	require.Equal(t, httpx.CodeInvalidArgument, httpx.ErrorFrom(err).Code)
	require.Empty(t, jobSvc.created)
}

func TestFillRunServiceCreateJobFailureMarksRunFailed(t *testing.T) {
	workspaceID := uuid.New()
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx"}))
	fillRepo := newFakeFillRunRepo()
	service := formpkg.NewFillRunService(fillRepo, formRepo, &fakeJobUseCase{err: errors.New("queue unavailable")}, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID, TargetNamespace: "target"}, auth.Principal{UserID: uuid.New()})

	require.Error(t, err)
	require.Len(t, fillRepo.runs, 1)
	for _, run := range fillRepo.runs {
		require.Equal(t, formpkg.FillRunStatusFailed, run.Status)
		require.Contains(t, run.ErrorMessage, "queue unavailable")
	}
}

func TestFillRunServiceDownloadArtifactByType(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	artifactID := uuid.New()
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusSucceeded}))
	artifacts := &fakeFillArtifactService{
		artifacts: []artifact.RunArtifact{{ID: uuid.New(), ArtifactType: artifact.TypeRunSummary}, {ID: artifactID, ArtifactType: artifact.TypeFilledForm}},
		download:  &artifact.DownloadResult{Filename: "filled.xlsx", ContentType: "application/octet-stream", ContentLength: 6, Reader: io.NopCloser(strings.NewReader("result"))},
	}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), &fakeJobUseCase{}, artifacts, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	result, err := service.GetDownloadArtifactByType(context.Background(), runID, artifact.TypeFilledForm, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Equal(t, "filled.xlsx", result.Filename)
	require.Equal(t, []uuid.UUID{runID}, artifacts.listCalls)
	require.Equal(t, []uuid.UUID{artifactID}, artifacts.downloadCalls)
}

func TestFillRunServiceDownloadArtifactByTypeNotFound(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusSucceeded}))
	artifacts := &fakeFillArtifactService{artifacts: []artifact.RunArtifact{{ID: uuid.New(), ArtifactType: artifact.TypeRunSummary}}}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), &fakeJobUseCase{}, artifacts, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.GetDownloadArtifactByType(context.Background(), runID, artifact.TypeFilledForm, auth.Principal{UserID: uuid.New()})

	require.Error(t, err)
	require.Equal(t, httpx.CodeNotFound, httpx.ErrorFrom(err).Code)
	require.Empty(t, artifacts.downloadCalls)
}
