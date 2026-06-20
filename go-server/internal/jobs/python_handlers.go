package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
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
	ReviewImporter       ReviewImporter
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

type ReviewImporter interface {
	ImportForFillRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, manifest *python.RunManifest) (ReviewImportResult, error)
}

type ReviewImportResult struct {
	TotalParsed      int
	Created          int
	Updated          int
	Skipped          int
	ParseErrors      int
	ReviewRequired   int
	WritebackAllowed int
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

func WithReviewImporter(importer ReviewImporter) FillFormPythonHandlerOption {
	return func(h *FillFormPythonHandler) {
		h.ReviewImporter = importer
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
	actor := auth.Principal{UserID: job.CreatedBy, Roles: []string{auth.RoleAdmin}}
	registered, err := h.Archiver.ArchiveStep15Artifacts(ctx, job.WorkspaceID, job.ResourceID, result.Manifest, actor)
	if err != nil {
		h.markFailed(context.Background(), payload.FillRunID, err)
		return err
	}
	h.emit(ctx, job, runevent.EventArtifactsRegistered, map[string]any{"count": len(registered)})
	if h.ReviewImporter != nil && payload.FillRunID != uuid.Nil {
		importResult, err := h.ReviewImporter.ImportForFillRun(ctx, job.WorkspaceID, payload.FillRunID, result.Manifest)
		if err != nil {
			h.emit(context.Background(), job, runevent.EventReviewImportFailed, map[string]any{"error_message": err.Error()})
			h.markFailed(context.Background(), payload.FillRunID, err)
			return err
		}
		h.emit(ctx, job, runevent.EventReviewItemsImported, map[string]any{
			"total_parsed":      importResult.TotalParsed,
			"created":           importResult.Created,
			"updated":           importResult.Updated,
			"parse_errors":      importResult.ParseErrors,
			"review_required":   importResult.ReviewRequired,
			"writeback_allowed": importResult.WritebackAllowed,
		})
	}
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

func (h *FillFormPythonHandler) RecoverInterruptedJob(ctx context.Context, job *Job, terminalStatus string, err error) {
	if h == nil || job == nil || job.ResourceID == uuid.Nil {
		return
	}
	if terminalStatus == JobStatusCanceled {
		h.markCanceled(ctx, job.ResourceID)
		return
	}
	if err == nil {
		err = errors.New("worker interrupted fill run")
	}
	h.markFailed(ctx, job.ResourceID, err)
}

type IngestKnowledgePythonHandler struct {
	Runner       python.Runner
	Events       RunEventWriter
	Logger       *zap.Logger
	Enabled      bool
	Materializer IngestionMaterializer
	Lifecycle    IngestionLifecycle
	Archiver     *python.ArtifactArchiver
}

type IngestKnowledgePythonHandlerOption func(*IngestKnowledgePythonHandler)

type IngestionMaterializer interface {
	MaterializeDocuments(ctx context.Context, workspaceID uuid.UUID, knowledgeBaseID uuid.UUID, outDir string) (inputDir string, documentCount int, cleanup func(), err error)
}

type IngestionLifecycle interface {
	MarkIngestionRunning(ctx context.Context, ingestionJobID uuid.UUID, jobID uuid.UUID) error
	MarkIngestionSucceeded(ctx context.Context, ingestionJobID uuid.UUID, result *python.IngestionResult) error
	MarkIngestionFailed(ctx context.Context, ingestionJobID uuid.UUID, err error) error
	MarkIngestionCanceled(ctx context.Context, ingestionJobID uuid.UUID) error
}

func WithIngestionMaterializer(materializer IngestionMaterializer) IngestKnowledgePythonHandlerOption {
	return func(h *IngestKnowledgePythonHandler) {
		h.Materializer = materializer
	}
}

func WithIngestionLifecycle(lifecycle IngestionLifecycle) IngestKnowledgePythonHandlerOption {
	return func(h *IngestKnowledgePythonHandler) {
		h.Lifecycle = lifecycle
	}
}

func WithIngestionArtifactArchiver(archiver *python.ArtifactArchiver) IngestKnowledgePythonHandlerOption {
	return func(h *IngestKnowledgePythonHandler) {
		h.Archiver = archiver
	}
}

func NewIngestKnowledgePythonHandler(runner python.Runner, events RunEventWriter, logger *zap.Logger, enabled bool, options ...IngestKnowledgePythonHandlerOption) *IngestKnowledgePythonHandler {
	if logger == nil {
		logger = zap.NewNop()
	}
	handler := &IngestKnowledgePythonHandler{Runner: runner, Events: events, Logger: logger, Enabled: enabled}
	for _, option := range options {
		if option != nil {
			option(handler)
		}
	}
	return handler
}

func (h *IngestKnowledgePythonHandler) Handle(ctx context.Context, job *Job) error {
	if job == nil {
		return errors.New("job is nil")
	}
	if h == nil {
		return fmt.Errorf("%w: ingest-knowledge handler is not configured", ErrHandlerNotImplemented)
	}
	var payload ingestKnowledgePythonPayload
	if err := decodeJobPayload(job.Payload, &payload); err != nil {
		return err
	}
	if !h.Enabled {
		err := fmt.Errorf("%w: ingest-knowledge command is disabled", ErrHandlerNotImplemented)
		h.markIngestionFailed(context.Background(), payload.IngestionJobID, err)
		return err
	}
	if h.Runner == nil {
		return errors.New("python runner is not configured")
	}
	if strings.TrimSpace(payload.Namespace) == "" {
		return errors.New("ingest_knowledge payload namespace is required")
	}
	if strings.TrimSpace(payload.OutDir) == "" {
		return errors.New("ingest_knowledge payload out_dir is required")
	}
	if h.Lifecycle != nil && payload.IngestionJobID != uuid.Nil {
		if err := h.Lifecycle.MarkIngestionRunning(ctx, payload.IngestionJobID, job.ID); err != nil {
			h.Logger.Warn("mark ingestion running failed", zap.String("ingestion_job_id", payload.IngestionJobID.String()), zap.Error(err))
		}
	}
	h.emit(ctx, job, runevent.EventIngestionStarted, map[string]any{"out_dir": payload.OutDir, "ingestion_job_id": payload.IngestionJobID.String()})
	h.emit(ctx, job, runevent.EventPythonStarted, map[string]any{"out_dir": payload.OutDir})
	if strings.TrimSpace(payload.InputDir) == "" {
		if h.Materializer == nil {
			err := errors.New("ingest_knowledge payload input_dir is required when materializer is not configured")
			h.markIngestionFailed(context.Background(), payload.IngestionJobID, err)
			return err
		}
		knowledgeBaseID, err := uuid.Parse(strings.TrimSpace(payload.KnowledgeBaseID))
		if err != nil {
			err := errors.New("ingest_knowledge payload knowledge_base_id must be a UUID when materializing documents")
			h.markIngestionFailed(context.Background(), payload.IngestionJobID, err)
			return err
		}
		inputDir, documentCount, cleanup, err := h.Materializer.MaterializeDocuments(ctx, job.WorkspaceID, knowledgeBaseID, payload.OutDir)
		if cleanup != nil {
			defer cleanup()
		}
		if err != nil {
			h.markIngestionFailed(context.Background(), payload.IngestionJobID, err)
			return err
		}
		payload.InputDir = inputDir
		h.emit(ctx, job, runevent.EventIngestionMaterialized, map[string]any{"input_dir": inputDir, "document_count": documentCount})
	}
	externalID := strings.TrimSpace(payload.KnowledgeBaseExternalID)
	if externalID == "" {
		externalID = strings.TrimSpace(payload.KnowledgeBaseID)
	}
	ingestionID := payload.IngestionJobID
	if ingestionID == uuid.Nil {
		ingestionID = job.ResourceID
	}
	result, err := h.Runner.RunKnowledgeIngestion(ctx, python.IngestionRequest{
		WorkspaceID:      job.WorkspaceID,
		JobID:            job.ID,
		IngestionID:      ingestionID,
		ConfigPath:       payload.ConfigPath,
		InputDir:         payload.InputDir,
		Namespace:        payload.Namespace,
		KnowledgeBaseID:  externalID,
		QdrantCollection: payload.QdrantCollection,
		QdrantNamespace:  payload.QdrantNamespace,
		OutDir:           payload.OutDir,
		Resume:           payload.Resume,
		Env:              payload.Env,
	})
	if err != nil {
		h.emit(ctx, job, runevent.EventIngestionFailed, map[string]any{"error_message": err.Error()})
		if ctx.Err() != nil {
			h.markIngestionCanceled(context.Background(), payload.IngestionJobID)
		} else {
			h.markIngestionFailed(context.Background(), payload.IngestionJobID, err)
		}
		return err
	}
	if result == nil {
		err := errors.New("python runner returned nil ingestion result")
		h.markIngestionFailed(context.Background(), payload.IngestionJobID, err)
		return err
	}
	h.emit(ctx, job, runevent.EventIngestionFinished, map[string]any{"exit_code": result.ExitCode, "out_dir": result.OutDir, "manifest_path": result.ManifestPath})
	h.emit(ctx, job, runevent.EventPythonFinished, map[string]any{"exit_code": result.ExitCode, "out_dir": result.OutDir, "manifest_path": result.ManifestPath})
	if err := h.archiveIngestionArtifacts(ctx, job, payload, result); err != nil {
		h.markIngestionFailed(context.Background(), payload.IngestionJobID, err)
		return err
	}
	if h.Lifecycle != nil && payload.IngestionJobID != uuid.Nil {
		if err := h.Lifecycle.MarkIngestionSucceeded(context.Background(), payload.IngestionJobID, result); err != nil {
			h.Logger.Warn("mark ingestion succeeded failed", zap.String("ingestion_job_id", payload.IngestionJobID.String()), zap.Error(err))
		}
	}
	h.emit(ctx, job, runevent.EventIndexVersionReady, map[string]any{"ingestion_job_id": payload.IngestionJobID.String(), "index_version_id": payload.IndexVersionID.String()})
	return nil
}

func (h *IngestKnowledgePythonHandler) RecoverInterruptedJob(ctx context.Context, job *Job, terminalStatus string, err error) {
	if h == nil || job == nil || job.ResourceID == uuid.Nil {
		return
	}
	if terminalStatus == JobStatusCanceled {
		h.markIngestionCanceled(ctx, job.ResourceID)
		return
	}
	if err == nil {
		err = errors.New("worker interrupted ingestion run")
	}
	h.markIngestionFailed(ctx, job.ResourceID, err)
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
	IngestionJobID          uuid.UUID         `json:"ingestion_job_id"`
	WorkspaceID             uuid.UUID         `json:"workspace_id"`
	KnowledgeBaseID         string            `json:"knowledge_base_id"`
	IndexVersionID          uuid.UUID         `json:"index_version_id"`
	ConfigPath              string            `json:"config_path"`
	InputDir                string            `json:"input_dir"`
	Namespace               string            `json:"namespace"`
	KnowledgeBaseExternalID string            `json:"knowledge_base_id_external"`
	OutDir                  string            `json:"out_dir"`
	Resume                  bool              `json:"resume"`
	QdrantCollection        string            `json:"qdrant_collection"`
	QdrantNamespace         string            `json:"qdrant_namespace"`
	Env                     map[string]string `json:"env"`
}

func (h *IngestKnowledgePythonHandler) markIngestionFailed(ctx context.Context, ingestionJobID uuid.UUID, err error) {
	if h != nil && h.Lifecycle != nil && ingestionJobID != uuid.Nil {
		if markErr := h.Lifecycle.MarkIngestionFailed(ctx, ingestionJobID, err); markErr != nil {
			h.Logger.Warn("mark ingestion failed failed", zap.String("ingestion_job_id", ingestionJobID.String()), zap.Error(markErr))
		}
	}
}

func (h *IngestKnowledgePythonHandler) markIngestionCanceled(ctx context.Context, ingestionJobID uuid.UUID) {
	if h != nil && h.Lifecycle != nil && ingestionJobID != uuid.Nil {
		if markErr := h.Lifecycle.MarkIngestionCanceled(ctx, ingestionJobID); markErr != nil {
			h.Logger.Warn("mark ingestion canceled failed", zap.String("ingestion_job_id", ingestionJobID.String()), zap.Error(markErr))
		}
	}
}

func (h *IngestKnowledgePythonHandler) archiveIngestionArtifacts(ctx context.Context, job *Job, payload ingestKnowledgePythonPayload, result *python.IngestionResult) error {
	if h == nil || h.Archiver == nil || result == nil || strings.TrimSpace(result.ManifestPath) == "" {
		return nil
	}
	if _, err := os.Stat(result.ManifestPath); err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	manifest, err := python.LoadRunManifest(result.ManifestPath)
	if err != nil {
		return err
	}
	actor := auth.Principal{UserID: job.CreatedBy, Roles: []string{auth.RoleAdmin}}
	ingestionID := payload.IngestionJobID
	if ingestionID == uuid.Nil {
		ingestionID = job.ResourceID
	}
	registered, err := h.Archiver.ArchiveStep15Artifacts(ctx, job.WorkspaceID, ingestionID, manifest, actor)
	if err != nil {
		return err
	}
	h.emit(ctx, job, runevent.EventArtifactsRegistered, map[string]any{"count": len(registered), "ingestion_job_id": ingestionID.String()})
	return nil
}
