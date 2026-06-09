package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"go.uber.org/zap"
)

type FillFormPythonHandler struct {
	Runner   python.Runner
	Archiver *python.ArtifactArchiver
	Events   RunEventWriter
	Logger   *zap.Logger
}

func NewFillFormPythonHandler(runner python.Runner, archiver *python.ArtifactArchiver, events RunEventWriter, logger *zap.Logger) *FillFormPythonHandler {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &FillFormPythonHandler{Runner: runner, Archiver: archiver, Events: events, Logger: logger}
}

func (h *FillFormPythonHandler) Handle(ctx context.Context, job *Job) error {
	if job == nil {
		return errors.New("job is nil")
	}
	if h == nil || h.Runner == nil {
		return errors.New("python runner is not configured")
	}
	var payload fillFormPythonPayload
	if err := decodeJobPayload(job.Payload, &payload); err != nil {
		return err
	}
	if strings.TrimSpace(payload.TargetNamespace) == "" {
		return errors.New("fill_form payload target_namespace is required")
	}
	if strings.TrimSpace(payload.OutDir) == "" {
		return errors.New("fill_form payload out_dir is required")
	}
	if payload.Writeback && strings.TrimSpace(payload.TemplatePath) == "" {
		return errors.New("fill_form payload template_path is required when writeback=true")
	}
	h.emit(ctx, job, runevent.EventPythonStarted, map[string]any{"out_dir": payload.OutDir})
	result, err := h.Runner.RunStep15Agent(ctx, python.Step15RunRequest{
		WorkspaceID:     job.WorkspaceID,
		JobID:           job.ID,
		RunID:           job.ResourceID,
		ConfigPath:      payload.ConfigPath,
		TargetNamespace: payload.TargetNamespace,
		GlobalNamespace: payload.GlobalNamespace,
		RoomContext:     payload.RoomContext,
		Rows:            payload.Rows,
		RetrievalMode:   payload.RetrievalMode,
		PromptVersion:   payload.PromptVersion,
		Judge:           payload.Judge,
		UseJudgeCache:   payload.UseJudgeCache,
		JudgeCachePath:  payload.JudgeCachePath,
		TemplatePath:    payload.TemplatePath,
		Writeback:       payload.Writeback,
		Resume:          payload.Resume,
		OutDir:          payload.OutDir,
		Env:             payload.Env,
	})
	if err != nil {
		h.emit(ctx, job, runevent.EventArtifactValidationFailed, map[string]any{"error_message": err.Error()})
		return err
	}
	if result == nil {
		return errors.New("python runner returned nil step15 result")
	}
	h.emit(ctx, job, runevent.EventPythonFinished, map[string]any{"exit_code": result.ExitCode, "out_dir": result.OutDir})
	if result.Validation != nil {
		if result.Validation.OK {
			h.emit(ctx, job, runevent.EventArtifactValidationSucceeded, map[string]any{"run_dir": result.Validation.RunDir})
		} else {
			h.emit(ctx, job, runevent.EventArtifactValidationFailed, map[string]any{"missing": result.Validation.Missing, "errors": result.Validation.Errors})
			return errors.New("artifact validation failed")
		}
	}
	if result.Manifest == nil {
		return errors.New("run manifest missing from python result")
	}
	if h.Archiver == nil {
		return errors.New("artifact archiver is not configured")
	}
	actor := auth.Principal{UserID: job.CreatedBy, Roles: []string{auth.RoleOperator}}
	registered, err := h.Archiver.ArchiveStep15Artifacts(ctx, job.WorkspaceID, job.ResourceID, result.Manifest, actor)
	if err != nil {
		return err
	}
	h.emit(ctx, job, runevent.EventArtifactsRegistered, map[string]any{"count": len(registered)})
	return nil
}

type IngestKnowledgePythonHandler struct {
	Runner  python.Runner
	Events  RunEventWriter
	Logger  *zap.Logger
	Enabled bool
}

func NewIngestKnowledgePythonHandler(runner python.Runner, events RunEventWriter, logger *zap.Logger, enabled bool) *IngestKnowledgePythonHandler {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &IngestKnowledgePythonHandler{Runner: runner, Events: events, Logger: logger, Enabled: enabled}
}

func (h *IngestKnowledgePythonHandler) Handle(ctx context.Context, job *Job) error {
	if job == nil {
		return errors.New("job is nil")
	}
	if h == nil || !h.Enabled {
		return fmt.Errorf("%w: ingest-knowledge command is disabled in Block 4 config", ErrHandlerNotImplemented)
	}
	if h.Runner == nil {
		return errors.New("python runner is not configured")
	}
	var payload ingestKnowledgePythonPayload
	if err := decodeJobPayload(job.Payload, &payload); err != nil {
		return err
	}
	if strings.TrimSpace(payload.InputDir) == "" {
		return errors.New("ingest_knowledge payload input_dir is required")
	}
	if strings.TrimSpace(payload.Namespace) == "" {
		return errors.New("ingest_knowledge payload namespace is required")
	}
	if strings.TrimSpace(payload.OutDir) == "" {
		return errors.New("ingest_knowledge payload out_dir is required")
	}
	h.emit(ctx, job, runevent.EventPythonStarted, map[string]any{"out_dir": payload.OutDir})
	result, err := h.Runner.RunKnowledgeIngestion(ctx, python.IngestionRequest{
		WorkspaceID:     job.WorkspaceID,
		JobID:           job.ID,
		IngestionID:     job.ResourceID,
		ConfigPath:      payload.ConfigPath,
		InputDir:        payload.InputDir,
		Namespace:       payload.Namespace,
		KnowledgeBaseID: payload.KnowledgeBaseID,
		OutDir:          payload.OutDir,
		Resume:          payload.Resume,
		Env:             payload.Env,
	})
	if err != nil {
		return err
	}
	if result == nil {
		return errors.New("python runner returned nil ingestion result")
	}
	h.emit(ctx, job, runevent.EventPythonFinished, map[string]any{"exit_code": result.ExitCode, "out_dir": result.OutDir, "manifest_path": result.ManifestPath})
	return nil
}

func (h *FillFormPythonHandler) emit(ctx context.Context, job *Job, eventType string, payload map[string]any) {
	emitPythonJobEvent(ctx, h.Events, job, eventType, payload)
}

func (h *IngestKnowledgePythonHandler) emit(ctx context.Context, job *Job, eventType string, payload map[string]any) {
	emitPythonJobEvent(ctx, h.Events, job, eventType, payload)
}

func emitPythonJobEvent(ctx context.Context, events RunEventWriter, job *Job, eventType string, payload map[string]any) {
	if events == nil || job == nil {
		return
	}
	if payload == nil {
		payload = map[string]any{}
	}
	payload["job_id"] = job.ID.String()
	payload["job_type"] = job.JobType
	jobID := job.ID
	_, _ = events.Create(ctx, runevent.RunEvent{
		WorkspaceID: job.WorkspaceID,
		RunID:       job.ResourceID,
		JobID:       &jobID,
		EventType:   eventType,
		Payload:     payload,
	})
}

func decodeJobPayload(payload map[string]any, target any) error {
	data, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("encode job payload: %w", err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		return fmt.Errorf("decode job payload: %w", err)
	}
	return nil
}

type fillFormPythonPayload struct {
	ConfigPath      string            `json:"config_path"`
	TargetNamespace string            `json:"target_namespace"`
	GlobalNamespace string            `json:"global_namespace"`
	RoomContext     string            `json:"room_context"`
	Rows            string            `json:"rows"`
	RetrievalMode   string            `json:"retrieval_mode"`
	PromptVersion   string            `json:"prompt_version"`
	Judge           bool              `json:"judge"`
	UseJudgeCache   bool              `json:"use_judge_cache"`
	JudgeCachePath  string            `json:"judge_cache_path"`
	TemplatePath    string            `json:"template_path"`
	Writeback       bool              `json:"writeback"`
	Resume          bool              `json:"resume"`
	OutDir          string            `json:"out_dir"`
	Env             map[string]string `json:"env"`
}

type ingestKnowledgePythonPayload struct {
	ConfigPath      string            `json:"config_path"`
	InputDir        string            `json:"input_dir"`
	Namespace       string            `json:"namespace"`
	KnowledgeBaseID string            `json:"knowledge_base_id"`
	OutDir          string            `json:"out_dir"`
	Resume          bool              `json:"resume"`
	Env             map[string]string `json:"env"`
}
