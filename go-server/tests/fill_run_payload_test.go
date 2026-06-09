package tests

import (
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestBuildFillFormJobPayloadIncludesIDs(t *testing.T) {
	cfg := *config.Default()
	run := formpkg.FillRun{ID: uuid.New(), WorkspaceID: uuid.New(), FormFileID: uuid.New(), TargetNamespace: "target", GlobalNamespace: "global", RowsSpec: "4-144", RetrievalMode: "layered", PromptVersion: "step15_compat", WritebackEnabled: true, OutDir: "out"}
	formFile := formpkg.FormFile{ID: run.FormFileID, WorkspaceID: run.WorkspaceID}

	payload := formpkg.BuildFillFormJobPayload(run, formFile, cfg)

	require.Equal(t, run.ID.String(), payload["fill_run_id"])
	require.Equal(t, run.WorkspaceID.String(), payload["workspace_id"])
	require.Equal(t, run.FormFileID.String(), payload["form_file_id"])
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
