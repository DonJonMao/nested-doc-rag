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
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestFillRunServiceCreateCreatesJobAndQueues(t *testing.T) {
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx", CreatedBy: actor.UserID}))
	fillRepo := newFakeFillRunRepo()
	jobSvc := &fakeJobUseCase{}
	audits := &fakeAuditRepo{}
	cfg := *config.Default()
	cfg.Python.ProjectDir = t.TempDir()
	service := formpkg.NewFillRunService(fillRepo, formRepo, jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, audit.NewService(audits, zap.NewNop()), zap.NewNop(), cfg)

	run, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID, Name: "西咸四号楼巡检", TargetNamespace: "target"}, actor)

	require.NoError(t, err)
	require.Equal(t, "西咸四号楼巡检", run.Name)
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

func TestFillRunServiceCreateDefaultsNameFromFormFilename(t *testing.T) {
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "基地云机房信息调研表.xlsx", CreatedBy: actor.UserID}))
	cfg := *config.Default()
	cfg.Python.ProjectDir = t.TempDir()
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), formRepo, &fakeJobUseCase{}, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), cfg)

	run, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID, TargetNamespace: "target"}, actor)

	require.NoError(t, err)
	require.Equal(t, "基地云机房信息调研表", run.Name)
}

func TestFillRunServiceCreateRejectsTooLongName(t *testing.T) {
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx", CreatedBy: actor.UserID}))
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), formRepo, &fakeJobUseCase{}, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID, Name: strings.Repeat("测", 121), TargetNamespace: "target"}, actor)

	require.Error(t, err)
	require.Equal(t, httpx.CodeInvalidArgument, httpx.ErrorFrom(err).Code)
}

func TestFillRunServiceCancelCallsJobService(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	jobID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), JobID: &jobID, Status: formpkg.FillRunStatusRunning, CreatedBy: actor.UserID}))
	jobSvc := &fakeJobUseCase{cancel: &jobs.Job{ID: jobID, Status: jobs.JobStatusCancelRequested}}
	audits := &fakeAuditRepo{}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, audit.NewService(audits, zap.NewNop()), zap.NewNop(), *config.Default())

	run, err := service.CancelFillRun(context.Background(), runID, actor)

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
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), JobID: &jobID, Status: formpkg.FillRunStatusCancelRequested, CreatedBy: actor.UserID}))
	jobSvc := &fakeJobUseCase{cancel: &jobs.Job{ID: jobID, Status: jobs.JobStatusCanceled}}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	run, err := service.CancelFillRun(context.Background(), runID, actor)

	require.NoError(t, err)
	require.Equal(t, formpkg.FillRunStatusCanceled, run.Status)
}

func TestFillRunServiceGetListPermissions(t *testing.T) {
	workspaceID := uuid.New()
	fillRepo := newFakeFillRunRepo()
	runID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusQueued, CreatedBy: actor.UserID}))
	authorizer := &fakeAuthorizer{}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), &fakeJobUseCase{}, &fakeFillArtifactService{}, authorizer, nil, zap.NewNop(), *config.Default())

	_, err := service.GetFillRun(context.Background(), runID, actor)
	require.NoError(t, err)
	runs, err := service.ListFillRuns(context.Background(), workspaceID, "", 50, 0, true, actor)

	require.NoError(t, err)
	require.Len(t, runs, 1)
	require.Equal(t, 0, authorizer.reads)
}

func TestFillRunServiceOperatorCannotAccessOtherUsersRuns(t *testing.T) {
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	otherUserID := uuid.New()
	ownRunID := uuid.New()
	otherRunID := uuid.New()
	otherJobID := uuid.New()
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: ownRunID, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusQueued, CreatedBy: actor.UserID}))
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: otherRunID, WorkspaceID: workspaceID, FormFileID: uuid.New(), JobID: &otherJobID, Status: formpkg.FillRunStatusRunning, CreatedBy: otherUserID}))
	jobSvc := &fakeJobUseCase{}
	artifacts := &fakeFillArtifactService{artifacts: []artifact.RunArtifact{{ID: uuid.New(), ArtifactType: artifact.TypeFilledForm}}}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), jobSvc, artifacts, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.GetFillRun(context.Background(), otherRunID, actor)
	requireAppError(t, err, httpx.CodeNotFound, http.StatusNotFound)

	_, err = service.CancelFillRun(context.Background(), otherRunID, actor)
	requireAppError(t, err, httpx.CodeNotFound, http.StatusNotFound)
	require.Empty(t, jobSvc.canceled)

	_, err = service.GetDownloadArtifactByType(context.Background(), otherRunID, artifact.TypeFilledForm, actor)
	requireAppError(t, err, httpx.CodeNotFound, http.StatusNotFound)
	require.Empty(t, artifacts.downloadCalls)

	runs, err := service.ListFillRuns(context.Background(), workspaceID, "", 50, 0, false, actor)
	require.NoError(t, err)
	require.Len(t, runs, 1)
	require.Equal(t, ownRunID, runs[0].ID)
}

func TestUserListsOnlyOwnFillRuns(t *testing.T) {
	workspaceID := uuid.New()
	userA := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	userB := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	runA := uuid.New()
	runB := uuid.New()
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runA, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusQueued, CreatedBy: userA.UserID}))
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runB, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusQueued, CreatedBy: userB.UserID}))
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), &fakeJobUseCase{}, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	userARuns, err := service.ListFillRuns(context.Background(), workspaceID, "", 50, 0, false, userA)
	require.NoError(t, err)
	require.Len(t, userARuns, 1)
	require.Equal(t, runA, userARuns[0].ID)

	userBRuns, err := service.ListFillRuns(context.Background(), workspaceID, "", 50, 0, false, userB)
	require.NoError(t, err)
	require.Len(t, userBRuns, 1)
	require.Equal(t, runB, userBRuns[0].ID)
}

func TestFillRunServiceAdminDoesNotSeeAllFillRunsByDefault(t *testing.T) {
	workspaceID := uuid.New()
	fillRepo := newFakeFillRunRepo()
	admin := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	adminRunID := uuid.New()
	otherRunID := uuid.New()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: adminRunID, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusQueued, CreatedBy: admin.UserID}))
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: otherRunID, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusQueued, CreatedBy: uuid.New()}))
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), &fakeJobUseCase{}, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	runs, err := service.ListFillRuns(context.Background(), workspaceID, "", 50, 0, false, admin)

	require.NoError(t, err)
	require.Len(t, runs, 1)
	require.Equal(t, adminRunID, runs[0].ID)

	_, err = service.GetFillRun(context.Background(), otherRunID, admin)
	requireAppError(t, err, httpx.CodeNotFound, http.StatusNotFound)
}

func TestFillRunServiceCreateDoesNotRequireWorkspaceWrite(t *testing.T) {
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx", CreatedBy: actor.UserID}))
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), formRepo, &fakeJobUseCase{}, &fakeFillArtifactService{}, &fakeAuthorizer{writeErr: httpx.NewAppError(httpx.CodeForbidden, "forbidden", http.StatusForbidden, nil, nil)}, nil, zap.NewNop(), *config.Default())

	run, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID, TargetNamespace: "target"}, actor)

	require.NoError(t, err)
	require.Equal(t, actor.UserID, run.CreatedBy)
}

func TestFillRunServiceCreateRejectsFormWorkspaceMismatch(t *testing.T) {
	requestWorkspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New()}
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: uuid.New(), FileID: uuid.New(), Filename: "form.xlsx", CreatedBy: actor.UserID}))
	jobSvc := &fakeJobUseCase{}
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), formRepo, jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: requestWorkspaceID, FormFileID: formID, TargetNamespace: "target"}, actor)

	require.Error(t, err)
	require.Equal(t, httpx.CodeForbidden, httpx.ErrorFrom(err).Code)
	require.Empty(t, jobSvc.created)
}

func TestFillRunServiceCreateRejectsOtherUsersFormFile(t *testing.T) {
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx", CreatedBy: uuid.New()}))
	jobSvc := &fakeJobUseCase{}
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), formRepo, jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID, TargetNamespace: "target"}, actor)

	requireAppError(t, err, httpx.CodeNotFound, http.StatusNotFound)
	require.Empty(t, jobSvc.created)
}

func TestFillRunServiceCreateRequiresTargetNamespace(t *testing.T) {
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx", CreatedBy: actor.UserID}))
	jobSvc := &fakeJobUseCase{}
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), formRepo, jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID}, actor)

	require.Error(t, err)
	require.Equal(t, httpx.CodeInvalidArgument, httpx.ErrorFrom(err).Code)
	require.Empty(t, jobSvc.created)
}

func TestFillRunServiceCreateJobFailureMarksRunFailed(t *testing.T) {
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx", CreatedBy: actor.UserID}))
	fillRepo := newFakeFillRunRepo()
	service := formpkg.NewFillRunService(fillRepo, formRepo, &fakeJobUseCase{err: errors.New("queue unavailable")}, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID, TargetNamespace: "target"}, actor)

	require.Error(t, err)
	require.Len(t, fillRepo.runs, 1)
	for _, run := range fillRepo.runs {
		require.Equal(t, formpkg.FillRunStatusFailed, run.Status)
		require.Contains(t, run.ErrorMessage, "queue unavailable")
	}
}

func TestFillRunServiceCreateForOperatorRequiresReadyKnowledgeBase(t *testing.T) {
	workspaceID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	formRepo := newFakeFormFileRepo()
	formID := uuid.New()
	kbID := uuid.New()
	currentVersionID := uuid.New()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx", CreatedBy: actor.UserID}))
	bases := newFakeKnowledgeBaseRepo()
	require.NoError(t, bases.Create(context.Background(), knowledgepkg.KnowledgeBase{
		ID:                    kbID,
		WorkspaceID:           workspaceID,
		Name:                  "西咸4号楼",
		Namespace:             "xixian_4",
		Status:                knowledgepkg.KnowledgeBaseStatusReady,
		CurrentIndexVersionID: &currentVersionID,
	}))
	jobSvc := &fakeJobUseCase{}
	cfg := *config.Default()
	cfg.Python.ProjectDir = t.TempDir()
	cfg.Python.Step15DefaultRows = "4-144"
	cfg.Python.Step15DefaultRetrievalMode = "layered"
	cfg.Python.Step15DefaultPromptVersion = "step15_compat"
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), formRepo, jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), cfg)
	service.SetKnowledgeBaseReader(bases)
	wrongVersionID := uuid.New()
	writeback := false

	run, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{
		WorkspaceID:     workspaceID,
		FormFileID:      formID,
		KnowledgeBaseID: &kbID,
		IndexVersionID:  &wrongVersionID,
		TargetNamespace: "other",
		Rows:            "1-999",
		RetrievalMode:   "flat",
		PromptVersion:   "debug",
		Judge:           true,
		UseJudgeCache:   true,
		Writeback:       &writeback,
	}, actor)

	require.Error(t, err)
	require.Nil(t, run)
	require.Equal(t, httpx.CodeConflict, httpx.ErrorFrom(err).Code)
	require.Empty(t, jobSvc.created)

	run, err = service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{
		WorkspaceID:     workspaceID,
		FormFileID:      formID,
		KnowledgeBaseID: &kbID,
		Rows:            "1-999",
		RetrievalMode:   "flat",
		PromptVersion:   "debug",
		Judge:           true,
		UseJudgeCache:   true,
		Writeback:       &writeback,
	}, actor)

	require.NoError(t, err)
	require.Equal(t, "xixian_4", run.TargetNamespace)
	require.Equal(t, currentVersionID, *run.IndexVersionID)
	require.Equal(t, "4-144", run.RowsSpec)
	require.Equal(t, "layered", run.RetrievalMode)
	require.Equal(t, "step15_compat", run.PromptVersion)
	require.False(t, run.JudgeEnabled)
	require.False(t, run.UseJudgeCache)
	require.True(t, run.WritebackEnabled)
}

func TestFillRunServiceDownloadArtifactByType(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	artifactID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusSucceeded, CreatedBy: actor.UserID}))
	artifacts := &fakeFillArtifactService{
		artifacts: []artifact.RunArtifact{{ID: uuid.New(), ArtifactType: artifact.TypeRunSummary}, {ID: artifactID, ArtifactType: artifact.TypeFilledForm}},
		download:  &artifact.DownloadResult{Filename: "filled.xlsx", ContentType: "application/octet-stream", ContentLength: 6, Reader: io.NopCloser(strings.NewReader("result"))},
	}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), &fakeJobUseCase{}, artifacts, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	result, err := service.GetDownloadArtifactByType(context.Background(), runID, artifact.TypeFilledForm, actor)

	require.NoError(t, err)
	require.Equal(t, "filled.xlsx", result.Filename)
	require.Equal(t, []uuid.UUID{runID}, artifacts.listCalls)
	require.Equal(t, []uuid.UUID{artifactID}, artifacts.downloadCalls)
}

func TestFillRunServiceDownloadArtifactByTypeNotFound(t *testing.T) {
	workspaceID := uuid.New()
	runID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}
	fillRepo := newFakeFillRunRepo()
	require.NoError(t, fillRepo.Create(context.Background(), formpkg.FillRun{ID: runID, WorkspaceID: workspaceID, FormFileID: uuid.New(), Status: formpkg.FillRunStatusSucceeded, CreatedBy: actor.UserID}))
	artifacts := &fakeFillArtifactService{artifacts: []artifact.RunArtifact{{ID: uuid.New(), ArtifactType: artifact.TypeRunSummary}}}
	service := formpkg.NewFillRunService(fillRepo, newFakeFormFileRepo(), &fakeJobUseCase{}, artifacts, &fakeAuthorizer{}, nil, zap.NewNop(), *config.Default())

	_, err := service.GetDownloadArtifactByType(context.Background(), runID, artifact.TypeFilledForm, actor)

	require.Error(t, err)
	require.Equal(t, httpx.CodeNotFound, httpx.ErrorFrom(err).Code)
	require.Empty(t, artifacts.downloadCalls)
}
