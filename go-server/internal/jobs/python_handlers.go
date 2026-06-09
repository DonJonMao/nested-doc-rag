package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type FillFormPythonHandler struct {
	Runner               python.Runner
	Archiver             *python.ArtifactArchiver
	Events               RunEventWriter
	Logger               *zap.Logger
	TemplateMaterializer TemplateMaterializer
	Lifecycle            FillRunLifecycle
}

type FillFormPythonHandlerOption func(*FillFormPythonHandler)

type TemplateMaterializer interface {
	MaterializeTemplate(ctx context.Context, workspaceID uuid.UUID, formFileID uuid.UUID, outDir string) (localPath string, cleanup func(), err error)
}

type FillRunLifecycle interface {
	MarkFillRunRunning(ctx context.Context, runID uuid.UUID, jobID uuid.UUID) error
	MarkFillRunSucceeded(ctx context.Context, runID uuid.UUID, result *python.Step15RunResult, artifacts []artifact.RunArtifact) error
	MarkFillRunCompletedWithFailures(ctx context.Context, runID uuid.UUID, result *python.Step15RunResult, artifacts []artifact.RunArtifact, errMsg string) error
	MarkFillRunFailed(ctx context.Context, runID uuid.UUID, err error) error
	MarkFillRunCanceled(ctx context.Context, runID uuid.UUID) error
}

func WithTemplateMaterializer(materializer TemplateMaterializer) FillFormPythonHandlerOption {
	return func(h *FillFormPythonHandler) {
		h.TemplateMaterializer = materializer
	}
}

func WithFillRunLifecycle(lifecycle FillRunLifecycle) FillFormPythonHandlerOption {
	return func(h *FillFormPythonHandler) {
		h.Lifecycle = lifecycle
	}
}

func NewFillFormPythonHandler(runner python.Runner, archiver *python.ArtifactArchiver, events RunEventWriter, logger *zap.Logger, options ...FillFormPythonHandlerOption) *FillFormPythonHandler {
	if logger == nil {
		logger = zap.NewNop()
	}
	handler := &FillFormPythonHandler{Runner: runner, Archiver: archiver, Events: events, Logger: logger}
	for _, option := range options {
		if option != nil {
			option(handler)
		}
	}
	return handler
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
		if h.TemplateMaterializer == nil {
			err := errors.New("fill_form payload template_path is required when writeback=true")
			h.markFailed(ctx, payload.FillRunID, err)
			return err
		}
		localPath, cleanup, err := h.TemplateMaterializer.MaterializeTemplate(ctx, job.WorkspaceID, payload.FormFileID, payload.OutDir)
		if cleanup != nil {
			defer cleanup()
		}
		if err != nil {
			h.markFailed(ctx, payload.FillRunID, err)
			return err
		}
		payload.TemplatePath = localPath
	}
	if h.Lifecycle != nil && payload.FillRunID != uuid.Nil {
		if err := h.Lifecycle.MarkFillRunRunning(ctx, payload.FillRunID, job.ID); err != nil {
			h.Logger.Warn("mark fill run running failed", zap.String("run_id", payload.FillRunID.String()), zap.Error(err))
		}
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
		if ctx.Err() != nil {
			h.markCanceled(context.Background(), payload.FillRunID)
		} else {
			h.markFailed(context.Background(), payload.FillRunID, err)
		}
		return err
	}
	if result == nil {
		err := errors.New("python runner returned nil step15 result")
		h.markFailed(context.Background(), payload.FillRunID, err)
		return err
	}
	h.emit(ctx, job, runevent.EventPythonFinished, map[string]any{"exit_code": result.ExitCode, "out_dir": result.OutDir})
	if result.Validation != nil {
		if result.Validation.OK {
			h.emit(ctx, job, runevent.EventArtifactValidationSucceeded, map[string]any{"run_dir": result.Validation.RunDir})
		} else {
			h.emit(ctx, job, runevent.EventArtifactValidationFailed, map[string]any{"missing": result.Validation.Missing, "errors": result.Validation.Errors})
			err := errors.New("artifact validation failed")
			h.markFailed(context.Background(), payload.FillRunID, err)
			return err
		}
	}
	if result.Manifest == nil {
		err := errors.New("run manifest missing from python result")
		h.markFailed(context.Background(), payload.FillRunID, err)
		return err
	}
	if h.Archiver == nil {
		err := errors.New("artifact archiver is not configured")
		h.markFailed(context.Background(), payload.FillRunID, err)
		return err
	}
	actor := auth.Principal{UserID: job.CreatedBy, Roles: []string{auth.RoleOperator}}
	registered, err := h.Archiver.ArchiveStep15Artifacts(ctx, job.WorkspaceID, job.ResourceID, result.Manifest, actor)
	if err != nil {
		h.markFailed(context.Background(), payload.FillRunID, err)
		return err
	}
	h.emit(ctx, job, runevent.EventArtifactsRegistered, map[string]any{"count": len(registered)})
	if h.Lifecycle != nil && payload.FillRunID != uuid.Nil {
		if result.Manifest.Status == JobStatusCompletedWithFailures || result.Manifest.Counts.Failed > 0 {
			if err := h.Lifecycle.MarkFillRunCompletedWithFailures(context.Background(), payload.FillRunID, result, registered, "completed with failures"); err != nil {
				h.Logger.Warn("mark fill run completed_with_failures failed", zap.String("run_id", payload.FillRunID.String()), zap.Error(err))
			}
		} else if err := h.Lifecycle.MarkFillRunSucceeded(context.Background(), payload.FillRunID, result, registered); err != nil {
			h.Logger.Warn("mark fill run succeeded failed", zap.String("run_id", payload.FillRunID.String()), zap.Error(err))
		}
	}
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

func (h *FillFormPythonHandler) markFailed(ctx context.Context, runID uuid.UUID, err error) {
	if h != nil && h.Lifecycle != nil && runID != uuid.Nil {
		if markErr := h.Lifecycle.MarkFillRunFailed(ctx, runID, err); markErr != nil {
			h.Logger.Warn("mark fill run failed failed", zap.String("run_id", runID.String()), zap.Error(markErr))
		}
	}
}

func (h *FillFormPythonHandler) markCanceled(ctx context.Context, runID uuid.UUID) {
	if h != nil && h.Lifecycle != nil && runID != uuid.Nil {
		if markErr := h.Lifecycle.MarkFillRunCanceled(ctx, runID); markErr != nil {
			h.Logger.Warn("mark fill run canceled failed", zap.String("run_id", runID.String()), zap.Error(markErr))
		}
	}
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
	FillRunID   uuid.UUID `json:"fill_run_id"`
	WorkspaceID uuid.UUID `json:"workspace_id"`
	FormFileID  uuid.UUID `json:"form_file_id"`

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
