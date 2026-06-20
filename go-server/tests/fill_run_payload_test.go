package tests

import (
	"context"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestBuildFillFormJobPayloadIncludesExecutionFields(t *testing.T) {
	cfg := *config.Default()
	cfg.Python.ConfigPath = "config/local.yaml"
	run := formpkg.FillRun{
		ID:               uuid.New(),
		WorkspaceID:      uuid.New(),
		FormFileID:       uuid.New(),
		TargetNamespace:  "target",
		GlobalNamespace:  "global",
		RoomContext:      "room",
		RowsSpec:         "4-144",
		RetrievalMode:    "layered",
		PromptVersion:    "step15_compat",
		JudgeEnabled:     true,
		UseJudgeCache:    true,
		WritebackEnabled: true,
		OutDir:           "out",
	}
	formFile := formpkg.FormFile{ID: run.FormFileID, WorkspaceID: run.WorkspaceID}

	payload := formpkg.BuildFillFormJobPayload(run, formFile, cfg)

	require.Equal(t, run.ID.String(), payload["fill_run_id"])
	require.Equal(t, run.WorkspaceID.String(), payload["workspace_id"])
	require.Equal(t, run.FormFileID.String(), payload["form_file_id"])
	require.Equal(t, "config/local.yaml", payload["config_path"])
	require.Equal(t, "target", payload["target_namespace"])
	require.Equal(t, "global", payload["global_namespace"])
	require.Equal(t, "room", payload["room_context"])
	require.Equal(t, "4-144", payload["rows"])
	require.Equal(t, "layered", payload["retrieval_mode"])
	require.Equal(t, "step15_compat", payload["prompt_version"])
	require.Equal(t, true, payload["judge"])
	require.Equal(t, true, payload["use_judge_cache"])
	require.Equal(t, true, payload["writeback"])
	require.Equal(t, true, payload["resume"])
	require.Equal(t, "out", payload["out_dir"])
	require.Empty(t, payload["template_path"])
}

func TestParseFillFormJobPayload(t *testing.T) {
	runID := uuid.New()
	workspaceID := uuid.New()
	formID := uuid.New()

	payload, err := formpkg.ParseFillFormJobPayload(map[string]any{
		"fill_run_id":      runID.String(),
		"workspace_id":     workspaceID.String(),
		"form_file_id":     formID.String(),
		"target_namespace": "target",
		"rows":             "4-144",
		"retrieval_mode":   "layered",
		"prompt_version":   "step15_compat",
		"out_dir":          "out",
	})

	require.NoError(t, err)
	require.Equal(t, runID, payload.FillRunID)
	require.Equal(t, workspaceID, payload.WorkspaceID)
	require.Equal(t, formID, payload.FormFileID)
}

func TestParseFillFormJobPayloadMalformed(t *testing.T) {
	_, err := formpkg.ParseFillFormJobPayload(map[string]any{
		"fill_run_id":      123,
		"workspace_id":     uuid.NewString(),
		"form_file_id":     uuid.NewString(),
		"target_namespace": "target",
	})

	require.Error(t, err)
	require.Contains(t, err.Error(), "decode fill form payload")
}

func TestCreateFillRunAppliesPythonDefaultsToPayload(t *testing.T) {
	workspaceID := uuid.New()
	formID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
	formRepo := newFakeFormFileRepo()
	require.NoError(t, formRepo.Create(context.Background(), formpkg.FormFile{ID: formID, WorkspaceID: workspaceID, FileID: uuid.New(), Filename: "form.xlsx", CreatedBy: actor.UserID}))
	jobSvc := &fakeJobUseCase{}
	cfg := *config.Default()
	cfg.Python.ProjectDir = t.TempDir()
	cfg.Python.ConfigPath = "config/local.yaml"
	cfg.Python.Step15DefaultRows = "7-9"
	cfg.Python.Step15DefaultRetrievalMode = "flat"
	cfg.Python.Step15DefaultPromptVersion = "prompt_v2"
	service := formpkg.NewFillRunService(newFakeFillRunRepo(), formRepo, jobSvc, &fakeFillArtifactService{}, &fakeAuthorizer{}, nil, zap.NewNop(), cfg)

	run, err := service.CreateFillRun(context.Background(), formpkg.CreateFillRunRequest{WorkspaceID: workspaceID, FormFileID: formID, TargetNamespace: "target"}, actor)

	require.NoError(t, err)
	require.Equal(t, "7-9", run.RowsSpec)
	require.Equal(t, "flat", run.RetrievalMode)
	require.Equal(t, "prompt_v2", run.PromptVersion)
	require.Equal(t, "7-9", jobSvc.created[0].Payload["rows"])
	require.Equal(t, "flat", jobSvc.created[0].Payload["retrieval_mode"])
	require.Equal(t, "prompt_v2", jobSvc.created[0].Payload["prompt_version"])
	require.Equal(t, "config/local.yaml", jobSvc.created[0].Payload["config_path"])
}
