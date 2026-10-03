package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
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
	ModelGateway         ModelGatewayEnvConfig
}

type FillFormPythonHandlerOption func(*FillFormPythonHandler)

type TemplateMaterializer interface {
	MaterializeTemplate(ctx context.Context, workspaceID uuid.UUID, formFileID uuid.UUID, outDir string) (localPath string, cleanup func(), err error)
}

type pinnedTemplateMaterializer interface {
	MaterializePinnedTemplate(context.Context, uuid.UUID, json.RawMessage, string) (string, func(), error)
}

type FillRunLifecycle interface {
	MarkFillRunRunning(ctx context.Context, runID uuid.UUID, jobID uuid.UUID) error
	MarkFillRunSucceeded(ctx context.Context, runID uuid.UUID, result *python.Step15RunResult, artifacts []artifact.RunArtifact) error
	MarkFillRunCompletedWithFailures(ctx context.Context, runID uuid.UUID, result *python.Step15RunResult, artifacts []artifact.RunArtifact, errMsg string) error
	MarkFillRunFailed(ctx context.Context, runID uuid.UUID, err error) error
	MarkFillRunCanceled(ctx context.Context, runID uuid.UUID) error
}

type fillRunProgressLifecycle interface {
	MarkFillRunProgress(ctx context.Context, runID uuid.UUID, progressDone int, progressTotal int) error
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

type ModelGatewayEnvConfig struct {
	Enabled          bool
	InternalBaseURL  string
	InternalTokenEnv string
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

func WithFillModelGatewayEnv(cfg config.ModelGatewayConfig) FillFormPythonHandlerOption {
	return func(h *FillFormPythonHandler) {
		h.ModelGateway = modelGatewayEnvConfig(cfg)
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
	indexScopesPath, err := freezeFillScopes(payload, job.WorkspaceID)
	if err != nil {
		h.markFailed(ctx, job.ResourceID, err)
		return err
	}
	runID := payload.FillRunID
	if runID == uuid.Nil {
		runID = job.ResourceID
	}
	if len(payload.TemplatePin) > 0 && string(payload.TemplatePin) != "null" {
		materializer, ok := h.TemplateMaterializer.(pinnedTemplateMaterializer)
		if !ok {
			return errors.New("frozen template materializer is required")
		}
		localPath, cleanup, err := materializer.MaterializePinnedTemplate(ctx, job.WorkspaceID, payload.TemplatePin, payload.OutDir)
		if cleanup != nil {
			defer cleanup()
		}
		if err != nil {
			h.markFailed(ctx, runID, err)
			return err
		}
		payload.TemplatePath = localPath
	} else if strings.TrimSpace(payload.TemplatePath) == "" {
		if h.TemplateMaterializer == nil {
			err := errors.New("fill_form payload template_path is required when materializer is not configured")
			h.markFailed(ctx, runID, err)
			return err
		}
		localPath, cleanup, err := h.TemplateMaterializer.MaterializeTemplate(ctx, job.WorkspaceID, payload.FormFileID, payload.OutDir)
		if cleanup != nil {
			defer cleanup()
		}
		if err != nil {
			h.markFailed(ctx, runID, err)
			return err
		}
		payload.TemplatePath = localPath
	}
	if h.Lifecycle != nil && runID != uuid.Nil {
		if err := h.Lifecycle.MarkFillRunRunning(ctx, runID, job.ID); err != nil {
			return err
		}
	}
	resume, err := effectiveFillResume(payload.OutDir, payload.Resume)
	if err != nil {
		h.markFailed(ctx, runID, err)
		return err
	}
	h.emit(ctx, job, runevent.EventPythonStarted, map[string]any{"out_dir": payload.OutDir})
	h.markInitialProgress(context.Background(), job, runID, payload.Rows)
	env := mergeModelGatewayEnv(payload.Env, h.ModelGateway, job, runID)
	stopProgress := h.startStep15ProgressWatcher(ctx, job, runID, payload.OutDir, payload.Rows)
	result, err := h.Runner.RunStep15Agent(ctx, python.Step15RunRequest{
		WorkspaceID:      job.WorkspaceID,
		JobID:            job.ID,
		RunID:            runID,
		ConfigPath:       payload.ConfigPath,
		TargetNamespace:  payload.TargetNamespace,
		GlobalNamespace:  payload.GlobalNamespace,
		IndexScopesPath:  indexScopesPath,
		QdrantCollection: frozenFillCollection(payload),
		RoomContext:      payload.RoomContext,
		Rows:             payload.Rows,
		RetrievalMode:    payload.RetrievalMode,
		PromptVersion:    payload.PromptVersion,
		Judge:            payload.Judge,
		UseJudgeCache:    payload.UseJudgeCache,
		JudgeCachePath:   payload.JudgeCachePath,
		TemplatePath:     payload.TemplatePath,
		Writeback:        payload.Writeback,
		Resume:           resume,
		OutDir:           payload.OutDir,
		Env:              env,
	})
	stopProgress()
	if err != nil {
		h.emit(ctx, job, runevent.EventArtifactValidationFailed, map[string]any{"error_message": err.Error()})
		if ctx.Err() != nil {
			h.markCanceled(context.Background(), runID)
		} else {
			h.markFailed(context.Background(), runID, err)
		}
		return err
	}
	if result == nil {
		err := errors.New("python runner returned nil step15 result")
		h.markFailed(context.Background(), runID, err)
		return err
	}
	h.emit(ctx, job, runevent.EventPythonFinished, map[string]any{"exit_code": result.ExitCode, "out_dir": result.OutDir})
	if result.Validation != nil {
		if result.Validation.OK {
			h.emit(ctx, job, runevent.EventArtifactValidationSucceeded, map[string]any{"run_dir": result.Validation.RunDir})
		} else {
			h.emit(ctx, job, runevent.EventArtifactValidationFailed, map[string]any{"missing": result.Validation.Missing, "errors": result.Validation.Errors})
			err := errors.New("artifact validation failed")
			h.markFailed(context.Background(), runID, err)
			return err
		}
	}
	if result.Manifest == nil {
		err := errors.New("run manifest missing from python result")
		h.markFailed(context.Background(), runID, err)
		return err
	}
	if h.Archiver == nil {
		err := errors.New("artifact archiver is not configured")
		h.markFailed(context.Background(), runID, err)
		return err
	}
	actor := auth.Principal{UserID: job.CreatedBy, Roles: []string{auth.RoleAdmin}}
	registered, err := h.Archiver.ArchiveStep15Artifacts(ctx, job.WorkspaceID, job.ResourceID, result.Manifest, actor)
	if err != nil {
		h.markFailed(context.Background(), runID, err)
		return err
	}
	h.emit(ctx, job, runevent.EventArtifactsRegistered, map[string]any{"count": len(registered)})
	if h.ReviewImporter != nil && runID != uuid.Nil {
		importResult, err := h.ReviewImporter.ImportForFillRun(ctx, job.WorkspaceID, runID, result.Manifest)
		if err != nil {
			h.emit(context.Background(), job, runevent.EventReviewImportFailed, map[string]any{"error_message": err.Error()})
			h.markFailed(context.Background(), runID, err)
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
	if h.Lifecycle != nil && runID != uuid.Nil {
		if result.Manifest.Status == JobStatusCompletedWithFailures || result.Manifest.Counts.Failed > 0 {
			if err := h.Lifecycle.MarkFillRunCompletedWithFailures(context.Background(), runID, result, registered, "completed with failures"); err != nil {
				return err
			}
		} else if err := h.Lifecycle.MarkFillRunSucceeded(context.Background(), runID, result, registered); err != nil {
			return err
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
	ModelGateway ModelGatewayEnvConfig
}

type IngestKnowledgePythonHandlerOption func(*IngestKnowledgePythonHandler)

type IngestionMaterializer interface {
	MaterializeDocuments(ctx context.Context, workspaceID uuid.UUID, knowledgeBaseID uuid.UUID, outDir string) (inputDir string, documentCount int, cleanup func(), err error)
}

type frozenIngestionMaterializer interface {
	MaterializeBuildInput(context.Context, uuid.UUID, json.RawMessage, string) (string, int, func(), error)
}

type publishedIngestionReader interface {
	ReadPublishedIngestion(context.Context, uuid.UUID) (*python.IngestionResult, bool, error)
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

func WithIngestionModelGatewayEnv(cfg config.ModelGatewayConfig) IngestKnowledgePythonHandlerOption {
	return func(h *IngestKnowledgePythonHandler) {
		h.ModelGateway = modelGatewayEnvConfig(cfg)
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
	if payload.versionedBuild() {
		reader, ok := h.Lifecycle.(publishedIngestionReader)
		if !ok {
			return errors.New("versioned ingestion publication recovery is required")
		}
		_, published, err := reader.ReadPublishedIngestion(ctx, payload.IngestionJobID)
		if err != nil {
			return err
		}
		if published {
			return nil
		} // ready data must never be cleared/rebuilt on job retry.
	}
	if h.Lifecycle != nil && payload.IngestionJobID != uuid.Nil {
		if err := h.Lifecycle.MarkIngestionRunning(ctx, payload.IngestionJobID, job.ID); err != nil {
			return err
		}
	}
	h.emit(ctx, job, runevent.EventIngestionStarted, map[string]any{"out_dir": payload.OutDir, "ingestion_job_id": payload.IngestionJobID.String()})
	h.emit(ctx, job, runevent.EventPythonStarted, map[string]any{"out_dir": payload.OutDir})
	inputSnapshotPath := ""
	if payload.versionedBuild() {
		var err error
		inputSnapshotPath, err = freezeBuildSnapshot(payload, job.WorkspaceID)
		if err != nil {
			h.markIngestionFailed(ctx, payload.IngestionJobID, err)
			return err
		}
		materializer, ok := h.Materializer.(frozenIngestionMaterializer)
		if !ok {
			return errors.New("frozen build materializer is required")
		}
		inputDir, documentCount, cleanup, err := materializer.MaterializeBuildInput(ctx, job.WorkspaceID, json.RawMessage(payload.InputSnapshotJSON), payload.OutDir)
		if cleanup != nil {
			defer cleanup()
		}
		if err != nil {
			h.markIngestionFailed(ctx, payload.IngestionJobID, err)
			return err
		}
		payload.InputDir = inputDir
		h.emit(ctx, job, runevent.EventIngestionMaterialized, map[string]any{"input_dir": inputDir, "document_count": documentCount})
	} else if strings.TrimSpace(payload.InputDir) == "" {
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
	if payload.versionedBuild() {
		externalID = payload.KnowledgeBaseID
	}
	if externalID == "" {
		externalID = strings.TrimSpace(payload.KnowledgeBaseID)
	}
	ingestionID := payload.IngestionJobID
	if ingestionID == uuid.Nil {
		ingestionID = job.ResourceID
	}
	env := mergeModelGatewayEnv(payload.Env, h.ModelGateway, job, ingestionID)
	result, err := h.Runner.RunKnowledgeIngestion(ctx, python.IngestionRequest{
		WorkspaceID:       job.WorkspaceID,
		JobID:             job.ID,
		IngestionID:       ingestionID,
		ConfigPath:        payload.ConfigPath,
		InputDir:          payload.InputDir,
		Namespace:         payload.Namespace,
		KnowledgeBaseID:   externalID,
		QdrantCollection:  payload.QdrantCollection,
		QdrantNamespace:   payload.QdrantNamespace,
		IndexVersionID:    payload.versionedID(),
		InputSnapshotPath: inputSnapshotPath,
		InputSnapshotHash: payload.InputSnapshotHash,
		OutDir:            payload.OutDir,
		Resume:            payload.Resume,
		Env:               env,
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
			return err
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

// RecoverPublishedJob repairs only the job state after the publication commit.
// It runs even for a still-running row left by a failed MarkSucceeded call.
func (h *IngestKnowledgePythonHandler) RecoverPublishedJob(ctx context.Context, job *Job) (bool, error) {
	if h == nil || job == nil {
		return false, nil
	}
	var payload ingestKnowledgePythonPayload
	if err := decodeJobPayload(job.Payload, &payload); err != nil {
		return false, err
	}
	if !payload.versionedBuild() {
		return false, nil
	}
	reader, ok := h.Lifecycle.(publishedIngestionReader)
	if !ok {
		return false, errors.New("versioned ingestion publication recovery is required")
	}
	_, published, err := reader.ReadPublishedIngestion(ctx, payload.IngestionJobID)
	return published, err
}

type step15TraceProgressState struct {
	seen          map[string]bool
	done          int
	total         int
	reportedTotal bool
}

type step15TraceEvent struct {
	FieldID string         `json:"field_id"`
	Step    string         `json:"step"`
	Payload map[string]any `json:"payload"`
}

func (h *FillFormPythonHandler) startStep15ProgressWatcher(ctx context.Context, job *Job, runID uuid.UUID, outDir string, rowsSpec string) func() {
	if h == nil || job == nil || strings.TrimSpace(outDir) == "" {
		return func() {}
	}
	watchCtx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() {
		defer close(done)
		state := step15TraceProgressState{seen: map[string]bool{}, total: estimateRowsTotal(rowsSpec)}
		ticker := time.NewTicker(1500 * time.Millisecond)
		defer ticker.Stop()
		tracePaths := step15TracePaths(outDir)
		for {
			state.scanPaths(h, job, runID, tracePaths)
			select {
			case <-watchCtx.Done():
				state.scanPaths(h, job, runID, tracePaths)
				return
			case <-ticker.C:
			}
		}
	}()
	return func() {
		cancel()
		<-done
	}
}

func (h *FillFormPythonHandler) markInitialProgress(ctx context.Context, job *Job, runID uuid.UUID, rowsSpec string) {
	total := estimateRowsTotal(rowsSpec)
	if total <= 0 {
		return
	}
	if updater, ok := h.Lifecycle.(fillRunProgressLifecycle); ok && runID != uuid.Nil {
		if err := updater.MarkFillRunProgress(ctx, runID, 0, total); err != nil {
			h.Logger.Warn("mark initial fill run progress failed", zap.String("run_id", runID.String()), zap.Error(err))
		}
	}
	h.emit(ctx, job, runevent.EventProgress, map[string]any{
		"message":        "任务已开始",
		"progress_done":  0,
		"progress_total": total,
	})
}

func step15TracePaths(outDir string) []string {
	return []string{
		filepath.Join(outDir, "trace.checkpoint.jsonl"),
		filepath.Join(outDir, "trace.jsonl"),
	}
}

func (s *step15TraceProgressState) scanPaths(h *FillFormPythonHandler, job *Job, runID uuid.UUID, tracePaths []string) {
	for _, tracePath := range tracePaths {
		s.scan(h, job, runID, tracePath)
	}
}

func (s *step15TraceProgressState) scan(h *FillFormPythonHandler, job *Job, runID uuid.UUID, tracePath string) {
	data, err := os.ReadFile(tracePath)
	if err != nil {
		return
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var event step15TraceEvent
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			continue
		}
		if event.Step == "run_started" {
			total := payloadInt(event.Payload, "", "selected_field_count")
			if total <= 0 {
				total = payloadInt(event.Payload, "", "fields_total")
			}
			if total > 0 && (!s.reportedTotal || total != s.total) {
				s.total = total
				s.reportedTotal = true
				if updater, ok := h.Lifecycle.(fillRunProgressLifecycle); ok && runID != uuid.Nil {
					if err := updater.MarkFillRunProgress(context.Background(), runID, s.done, total); err != nil {
						h.Logger.Warn("mark parsed fill run progress failed", zap.String("run_id", runID.String()), zap.Error(err))
					}
				}
				h.emit(context.Background(), job, runevent.EventProgress, map[string]any{
					"message":        "任务已开始",
					"progress_done":  s.done,
					"progress_total": total,
				})
			}
			continue
		}
		if event.Step != "field_completed" || event.FieldID == "" || s.seen[event.FieldID] {
			continue
		}
		s.seen[event.FieldID] = true
		s.done++
		predictionKey := "final_prediction"
		if _, ok := event.Payload[predictionKey]; !ok {
			predictionKey = "raw_prediction"
		}
		rowIndex := payloadInt(event.Payload, predictionKey, "row_index")
		status := payloadString(event.Payload, predictionKey, "answer_status")
		answerValue := payloadString(event.Payload, predictionKey, "answer_value")
		total := s.total
		if updater, ok := h.Lifecycle.(fillRunProgressLifecycle); ok && runID != uuid.Nil {
			if err := updater.MarkFillRunProgress(context.Background(), runID, s.done, total); err != nil {
				h.Logger.Warn("mark fill run progress failed", zap.String("run_id", runID.String()), zap.Error(err))
			}
		}
		message := "字段处理完成"
		if rowIndex > 0 {
			message = fmt.Sprintf("第 %d 行处理完成", rowIndex)
		}
		h.emit(context.Background(), job, runevent.EventProgress, map[string]any{
			"message":        message,
			"row_index":      rowIndex,
			"answer_status":  status,
			"answer_value":   answerValue,
			"progress_done":  s.done,
			"progress_total": total,
		})
	}
}

func estimateRowsTotal(rowsSpec string) int {
	total := 0
	for _, part := range strings.Split(rowsSpec, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		startText, endText, hasRange := strings.Cut(part, "-")
		start, err := strconv.Atoi(strings.TrimSpace(startText))
		if err != nil {
			continue
		}
		if !hasRange {
			total++
			continue
		}
		end, err := strconv.Atoi(strings.TrimSpace(endText))
		if err != nil {
			continue
		}
		if end < start {
			start, end = end, start
		}
		total += end - start + 1
	}
	return total
}

func payloadInt(payload map[string]any, parentKey string, key string) int {
	value := nestedPayloadValue(payload, parentKey, key)
	switch typed := value.(type) {
	case float64:
		return int(typed)
	case int:
		return typed
	case json.Number:
		parsed, _ := typed.Int64()
		return int(parsed)
	case string:
		parsed, _ := strconv.Atoi(strings.TrimSpace(typed))
		return parsed
	default:
		return 0
	}
}

func payloadString(payload map[string]any, parentKey string, key string) string {
	value := nestedPayloadValue(payload, parentKey, key)
	if value == nil {
		return ""
	}
	return strings.TrimSpace(fmt.Sprint(value))
}

func nestedPayloadValue(payload map[string]any, parentKey string, key string) any {
	if payload == nil {
		return nil
	}
	if parentKey == "" {
		return payload[key]
	}
	parent, ok := payload[parentKey].(map[string]any)
	if !ok || parent == nil {
		return nil
	}
	return parent[key]
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

func modelGatewayEnvConfig(cfg config.ModelGatewayConfig) ModelGatewayEnvConfig {
	return ModelGatewayEnvConfig{
		Enabled:          cfg.Enabled,
		InternalBaseURL:  strings.TrimSpace(cfg.InternalBaseURL),
		InternalTokenEnv: strings.TrimSpace(cfg.InternalTokenEnv),
	}
}

func mergeModelGatewayEnv(base map[string]string, cfg ModelGatewayEnvConfig, job *Job, runID uuid.UUID) map[string]string {
	if !cfg.Enabled {
		return base
	}
	env := make(map[string]string, len(base)+8)
	for key, value := range base {
		env[key] = value
	}
	env["NDR_MODEL_GATEWAY_ENABLED"] = "true"
	env["NDR_MODEL_GATEWAY_BASE_URL"] = strings.TrimRight(cfg.InternalBaseURL, "/")
	if cfg.InternalTokenEnv != "" {
		env["NDR_MODEL_GATEWAY_TOKEN"] = os.Getenv(cfg.InternalTokenEnv)
	}
	if runID != uuid.Nil {
		env["NDR_RUN_ID"] = runID.String()
	}
	if job != nil {
		if job.ID != uuid.Nil {
			env["NDR_JOB_ID"] = job.ID.String()
		}
		if job.CreatedBy != uuid.Nil {
			env["NDR_USER_ID"] = job.CreatedBy.String()
		}
		if job.WorkspaceID != uuid.Nil {
			env["NDR_WORKSPACE_ID"] = job.WorkspaceID.String()
		}
	}
	return env
}

type fillFormPythonPayload struct {
	FillRunID       uuid.UUID       `json:"fill_run_id"`
	WorkspaceID     uuid.UUID       `json:"workspace_id"`
	FormFileID      uuid.UUID       `json:"form_file_id"`
	TargetScope     json.RawMessage `json:"target_scope"`
	GlobalScope     json.RawMessage `json:"global_scope"`
	TemplatePin     json.RawMessage `json:"template_pin"`
	IndexScopesJSON string          `json:"index_scopes_json"`

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
	StorageContract         string            `json:"storage_contract"`
	InputSnapshotJSON       string            `json:"input_snapshot_json"`
	InputSnapshotHash       string            `json:"input_snapshot_hash"`
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

func (p ingestKnowledgePythonPayload) versionedBuild() bool {
	return p.StorageContract == "versioned_v1" || p.InputSnapshotJSON != "" || p.InputSnapshotHash != ""
}

func (p ingestKnowledgePythonPayload) versionedID() string {
	if !p.versionedBuild() {
		return ""
	}
	return nonNilUUIDString(p.IndexVersionID)
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
