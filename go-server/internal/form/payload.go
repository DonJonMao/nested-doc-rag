package form

import (
	"encoding/json"
	"fmt"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/google/uuid"
)

type FillFormJobPayload struct {
	FillRunID   uuid.UUID `json:"fill_run_id"`
	WorkspaceID uuid.UUID `json:"workspace_id"`
	FormFileID  uuid.UUID `json:"form_file_id"`

	ConfigPath      string `json:"config_path"`
	TargetNamespace string `json:"target_namespace"`
	GlobalNamespace string `json:"global_namespace"`
	RoomContext     string `json:"room_context"`
	Rows            string `json:"rows"`
	RetrievalMode   string `json:"retrieval_mode"`
	PromptVersion   string `json:"prompt_version"`

	Judge          bool   `json:"judge"`
	UseJudgeCache  bool   `json:"use_judge_cache"`
	JudgeCachePath string `json:"judge_cache_path"`

	TemplatePath string `json:"template_path"`
	Writeback    bool   `json:"writeback"`
	Resume       bool   `json:"resume"`
	OutDir       string `json:"out_dir"`
}

func BuildFillFormJobPayload(run FillRun, formFile FormFile, cfg config.Config) map[string]any {
	payload := FillFormJobPayload{
		FillRunID:       run.ID,
		WorkspaceID:     run.WorkspaceID,
		FormFileID:      formFile.ID,
		ConfigPath:      cfg.Python.ConfigPath,
		TargetNamespace: run.TargetNamespace,
		GlobalNamespace: run.GlobalNamespace,
		RoomContext:     run.RoomContext,
		Rows:            run.RowsSpec,
		RetrievalMode:   run.RetrievalMode,
		PromptVersion:   run.PromptVersion,
		Judge:           run.JudgeEnabled,
		UseJudgeCache:   run.UseJudgeCache,
		TemplatePath:    "",
		Writeback:       run.WritebackEnabled,
		Resume:          true,
		OutDir:          run.OutDir,
	}
	data, _ := json.Marshal(payload)
	var out map[string]any
	_ = json.Unmarshal(data, &out)
	return out
}

func ParseFillFormJobPayload(payload map[string]any) (FillFormJobPayload, error) {
	var parsed FillFormJobPayload
	data, err := json.Marshal(payload)
	if err != nil {
		return parsed, fmt.Errorf("encode fill form payload: %w", err)
	}
	if err := json.Unmarshal(data, &parsed); err != nil {
		return parsed, fmt.Errorf("decode fill form payload: %w", err)
	}
	return parsed, nil
}
