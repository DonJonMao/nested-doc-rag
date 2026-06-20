package form

import (
	"bufio"
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

const (
	ManifestStatusValid   = "valid"
	ManifestStatusInvalid = "invalid"
	ManifestStatusMissing = "missing"

	ArtifactValidationStatusValid   = "valid"
	ArtifactValidationStatusInvalid = "invalid"
	ArtifactValidationStatusMissing = "missing"

	SafeWritebackMessage = "该表格仅自动写入系统判定为安全的字段；未写入或需复核字段请人工补充。"

	filledFormContentType = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

type FillRunListItem struct {
	ID               uuid.UUID            `json:"id"`
	WorkspaceID      uuid.UUID            `json:"workspace_id"`
	Name             string               `json:"name,omitempty"`
	RawStatus        string               `json:"raw_status,omitempty"`
	Status           string               `json:"status"`
	CreatedAt        time.Time            `json:"created_at"`
	UpdatedAt        time.Time            `json:"updated_at"`
	CompletedAt      *time.Time           `json:"completed_at,omitempty"`
	TemplateFileName string               `json:"template_file_name,omitempty"`
	KBName           string               `json:"kb_name,omitempty"`
	Summary          FillRunSummaryCounts `json:"summary"`
	Downloads        FillRunDownloads     `json:"downloads"`
}

type FillRunDetail struct {
	ID                         uuid.UUID                `json:"id"`
	WorkspaceID                uuid.UUID                `json:"workspace_id"`
	Name                       string                   `json:"name,omitempty"`
	RawStatus                  string                   `json:"raw_status,omitempty"`
	Status                     string                   `json:"status"`
	CreatedAt                  time.Time                `json:"created_at"`
	UpdatedAt                  time.Time                `json:"updated_at"`
	CompletedAt                *time.Time               `json:"completed_at,omitempty"`
	TemplateFileName           string                   `json:"template_file_name,omitempty"`
	KBName                     string                   `json:"kb_name,omitempty"`
	ManifestStatus             string                   `json:"manifest_status"`
	ArtifactValidationStatus   string                   `json:"artifact_validation_status"`
	Message                    string                   `json:"message"`
	ErrorMessage               string                   `json:"error_message,omitempty"`
	Summary                    FillRunSummaryCounts     `json:"summary"`
	Artifacts                  FillRunArtifactDownloads `json:"artifacts"`
	ArtifactValidationWarnings []string                 `json:"artifact_validation_warnings,omitempty"`
}

type FillRunSummaryCounts struct {
	TotalFields        int `json:"total_fields"`
	Answered           int `json:"answered"`
	PartialClue        int `json:"partial_clue"`
	NotFound           int `json:"not_found"`
	ConflictUnresolved int `json:"conflict_unresolved"`
	WritebackAllowed   int `json:"writeback_allowed"`
	ReviewRequired     int `json:"review_required"`
	FailedFields       int `json:"failed_fields"`
}

type FillRunDownloads struct {
	FilledFormAvailable     bool `json:"filled_form_available"`
	ReviewItemsAvailable    bool `json:"review_items_available"`
	WritebackAuditAvailable bool `json:"writeback_audit_available"`
}

type FillRunArtifactDownloads struct {
	FilledForm     FillRunArtifactInfo `json:"filled_form"`
	ReviewItems    FillRunArtifactInfo `json:"review_items"`
	ReviewItemsCSV FillRunArtifactInfo `json:"review_items_csv"`
	WritebackAudit FillRunArtifactInfo `json:"writeback_audit"`
	Summary        FillRunArtifactInfo `json:"summary"`
}

type FillRunArtifactInfo struct {
	Available bool   `json:"available"`
	Filename  string `json:"filename,omitempty"`
	Size      int64  `json:"size,omitempty"`
}

type runManifestArtifact struct {
	RunID            string         `json:"run_id"`
	Status           string         `json:"status"`
	WritebackEnabled bool           `json:"writeback_enabled"`
	Artifacts        map[string]any `json:"artifacts"`
	Counts           map[string]any `json:"counts"`
}

type resultContext struct {
	run                      *FillRun
	artifacts                []artifact.RunArtifact
	artifactByType           map[string]artifact.RunArtifact
	manifest                 *runManifestArtifact
	manifestStatus           string
	artifactValidationStatus string
	warnings                 []string
	summary                  FillRunSummaryCounts
}

func (s *FillRunService) ListFillRunSummaries(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int, mine bool, actor auth.Principal) ([]FillRunListItem, error) {
	runs, err := s.ListFillRuns(ctx, workspaceID, status, limit, offset, mine, actor)
	if err != nil {
		return nil, err
	}
	out := make([]FillRunListItem, 0, len(runs))
	for _, run := range runs {
		item := s.buildListItem(ctx, run, actor)
		out = append(out, item)
	}
	return out, nil
}

func (s *FillRunService) GetFillRunDetail(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*FillRunDetail, error) {
	result, err := s.loadResultContext(ctx, runID, actor)
	if err != nil {
		return nil, err
	}
	run := result.run
	return &FillRunDetail{
		ID:                         run.ID,
		WorkspaceID:                run.WorkspaceID,
		Name:                       run.Name,
		RawStatus:                  run.Status,
		Status:                     publicFillRunStatus(run.Status),
		CreatedAt:                  run.CreatedAt,
		UpdatedAt:                  run.UpdatedAt,
		CompletedAt:                run.FinishedAt,
		TemplateFileName:           s.templateFileName(ctx, run.FormFileID),
		KBName:                     s.knowledgeBaseName(ctx, run.KnowledgeBaseID),
		ManifestStatus:             result.manifestStatus,
		ArtifactValidationStatus:   result.artifactValidationStatus,
		Message:                    SafeWritebackMessage,
		ErrorMessage:               run.ErrorMessage,
		Summary:                    result.summary,
		Artifacts:                  artifactDownloads(result.artifactByType),
		ArtifactValidationWarnings: result.warnings,
	}, nil
}

func (s *FillRunService) DownloadFilledForm(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	result, err := s.loadResultContext(ctx, runID, actor)
	if err != nil {
		return nil, err
	}
	if err := ensureFillRunDownloadReady(result.run); err != nil {
		return nil, err
	}
	if result.manifestStatus != ManifestStatusValid {
		return nil, httpx.NewAppError(httpx.CodeConflict, "run manifest is missing or invalid", http.StatusConflict, map[string]string{"manifest_status": result.manifestStatus}, nil)
	}
	if result.manifest == nil || !result.manifest.hasArtifact(artifact.TypeFilledForm) {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "filled form artifact is not declared by run manifest", http.StatusNotFound, nil, nil)
	}
	item, ok := result.artifactByType[artifact.TypeFilledForm]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "filled form artifact not found", http.StatusNotFound, nil, nil)
	}
	if result.artifactValidationStatus != ArtifactValidationStatusValid {
		return nil, httpx.NewAppError(httpx.CodeConflict, "artifact validation failed", http.StatusConflict, map[string]string{"artifact_validation_status": result.artifactValidationStatus}, nil)
	}
	download, err := s.artifacts.DownloadArtifactProxy(ctx, item.ID, actor)
	if err != nil {
		return nil, err
	}
	download.Filename = "filled_form.xlsx"
	download.ContentType = filledFormContentType
	return download, nil
}

func (s *FillRunService) DownloadReviewItems(ctx context.Context, runID uuid.UUID, format string, actor auth.Principal) (*artifact.DownloadResult, error) {
	format = strings.ToLower(strings.TrimSpace(format))
	if format == "" {
		format = "csv"
	}
	if format != "csv" && format != "jsonl" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid review_items format", http.StatusBadRequest, map[string]string{"format": format}, nil)
	}
	result, err := s.downloadRunArtifact(ctx, runID, artifact.TypeReviewItems, actor)
	if err != nil {
		return nil, err
	}
	if format == "jsonl" {
		result.Filename = "review_items.jsonl"
		if result.ContentType == "" || result.ContentType == "application/octet-stream" {
			result.ContentType = "application/x-ndjson; charset=utf-8"
		}
		return result, nil
	}
	reader := result.Reader
	pr, pw := io.Pipe()
	go func() {
		defer reader.Close()
		err := ReviewItemsJSONLToCSV(reader, pw)
		_ = pw.CloseWithError(err)
	}()
	return &artifact.DownloadResult{
		Filename:      "review_items.csv",
		ContentType:   "text/csv; charset=utf-8",
		ContentLength: -1,
		Reader:        pr,
	}, nil
}

func (s *FillRunService) DownloadWritebackAudit(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	result, err := s.downloadRunArtifact(ctx, runID, artifact.TypeWritebackAudit, actor)
	if err != nil {
		return nil, err
	}
	result.Filename = "writeback_audit.jsonl"
	if result.ContentType == "" || result.ContentType == "application/octet-stream" {
		result.ContentType = "application/x-ndjson; charset=utf-8"
	}
	return result, nil
}

func (s *FillRunService) DownloadSummary(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error) {
	result, err := s.downloadRunArtifact(ctx, runID, artifact.TypeSummary, actor)
	if err != nil {
		return nil, err
	}
	result.Filename = "summary.json"
	if result.ContentType == "" || result.ContentType == "application/octet-stream" {
		result.ContentType = "application/json; charset=utf-8"
	}
	return result, nil
}

func (s *FillRunService) downloadRunArtifact(ctx context.Context, runID uuid.UUID, artifactType string, actor auth.Principal) (*artifact.DownloadResult, error) {
	result, err := s.loadResultContext(ctx, runID, actor)
	if err != nil {
		return nil, err
	}
	if err := ensureFillRunDownloadReady(result.run); err != nil {
		return nil, err
	}
	item, ok := result.artifactByType[artifactType]
	if !ok {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "artifact not found", http.StatusNotFound, map[string]string{"artifact_type": artifactType}, nil)
	}
	return s.artifacts.DownloadArtifactProxy(ctx, item.ID, actor)
}

func (s *FillRunService) buildListItem(ctx context.Context, run FillRun, actor auth.Principal) FillRunListItem {
	result, err := s.loadResultContextForRun(ctx, &run, actor)
	if err != nil {
		s.logger.Warn("load fill run result context for list failed", zap.String("run_id", run.ID.String()), zap.Error(err))
		result = &resultContext{
			run:                      &run,
			artifactByType:           map[string]artifact.RunArtifact{},
			manifestStatus:           ManifestStatusMissing,
			artifactValidationStatus: ArtifactValidationStatusMissing,
			summary:                  summaryFromRun(run),
		}
	}
	return FillRunListItem{
		ID:               run.ID,
		WorkspaceID:      run.WorkspaceID,
		Name:             run.Name,
		RawStatus:        run.Status,
		Status:           publicFillRunStatus(run.Status),
		CreatedAt:        run.CreatedAt,
		UpdatedAt:        run.UpdatedAt,
		CompletedAt:      run.FinishedAt,
		TemplateFileName: s.templateFileName(ctx, run.FormFileID),
		KBName:           s.knowledgeBaseName(ctx, run.KnowledgeBaseID),
		Summary:          result.summary,
		Downloads:        downloadsForResult(result),
	}
}

func (s *FillRunService) loadResultContext(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*resultContext, error) {
	run, err := s.GetFillRun(ctx, runID, actor)
	if err != nil {
		return nil, err
	}
	return s.loadResultContextForRun(ctx, run, actor)
}

func (s *FillRunService) loadResultContextForRun(ctx context.Context, run *FillRun, actor auth.Principal) (*resultContext, error) {
	artifacts, err := s.artifacts.ListRunArtifacts(ctx, run.WorkspaceID, run.ID, actor)
	if err != nil {
		return nil, err
	}
	byType := latestArtifactByType(artifacts)
	manifest, manifestStatus, manifestWarnings := s.loadManifest(ctx, byType, actor)
	validationStatus, validationWarnings := validateManifestArtifacts(manifest, manifestStatus, byType)
	warnings := append(manifestWarnings, validationWarnings...)
	summary := summaryFromRun(*run)
	if summaryArtifact, ok := byType[artifact.TypeSummary]; ok {
		if parsed, err := s.loadSummaryCounts(ctx, summaryArtifact.ID, actor); err == nil {
			summary = mergeSummaryCounts(parsed, summary)
		} else {
			warnings = append(warnings, "summary artifact could not be parsed")
			s.logger.Warn("parse fill run summary artifact failed", zap.String("run_id", run.ID.String()), zap.Error(err))
		}
	} else if manifest != nil {
		summary = mergeSummaryCounts(summaryFromManifest(manifest), summary)
	}
	return &resultContext{
		run:                      run,
		artifacts:                artifacts,
		artifactByType:           byType,
		manifest:                 manifest,
		manifestStatus:           manifestStatus,
		artifactValidationStatus: validationStatus,
		warnings:                 warnings,
		summary:                  summary,
	}, nil
}

func (s *FillRunService) loadManifest(ctx context.Context, artifacts map[string]artifact.RunArtifact, actor auth.Principal) (*runManifestArtifact, string, []string) {
	item, ok := artifacts[artifact.TypeRunManifest]
	if !ok {
		return nil, ManifestStatusMissing, []string{"run_manifest artifact is missing"}
	}
	download, err := s.artifacts.OpenArtifact(ctx, item.ID, actor)
	if err != nil {
		return nil, ManifestStatusInvalid, []string{"run_manifest artifact could not be read"}
	}
	defer download.Reader.Close()
	var manifest runManifestArtifact
	decoder := json.NewDecoder(io.LimitReader(download.Reader, 10<<20))
	decoder.UseNumber()
	if err := decoder.Decode(&manifest); err != nil {
		return nil, ManifestStatusInvalid, []string{"run_manifest artifact is not valid JSON"}
	}
	if err := manifest.validate(); err != nil {
		return nil, ManifestStatusInvalid, []string{err.Error()}
	}
	return &manifest, ManifestStatusValid, nil
}

func (s *FillRunService) loadSummaryCounts(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (FillRunSummaryCounts, error) {
	download, err := s.artifacts.OpenArtifact(ctx, artifactID, actor)
	if err != nil {
		return FillRunSummaryCounts{}, err
	}
	defer download.Reader.Close()
	var raw map[string]any
	decoder := json.NewDecoder(io.LimitReader(download.Reader, 10<<20))
	decoder.UseNumber()
	if err := decoder.Decode(&raw); err != nil {
		return FillRunSummaryCounts{}, err
	}
	return summaryFromMap(raw), nil
}

func (s *FillRunService) templateFileName(ctx context.Context, formFileID uuid.UUID) string {
	if formFileID == uuid.Nil || s.forms == nil {
		return ""
	}
	formFile, err := s.forms.GetByID(ctx, formFileID)
	if err != nil {
		s.logger.Warn("load form file name failed", zap.String("form_file_id", formFileID.String()), zap.Error(err))
		return ""
	}
	return formFile.Filename
}

func (s *FillRunService) knowledgeBaseName(ctx context.Context, knowledgeBaseID *uuid.UUID) string {
	if knowledgeBaseID == nil || *knowledgeBaseID == uuid.Nil || s.kbs == nil {
		return ""
	}
	kb, err := s.kbs.GetByID(ctx, *knowledgeBaseID)
	if err != nil {
		s.logger.Warn("load knowledge base name failed", zap.String("knowledge_base_id", knowledgeBaseID.String()), zap.Error(err))
		return ""
	}
	return kb.Name
}

func latestArtifactByType(artifacts []artifact.RunArtifact) map[string]artifact.RunArtifact {
	out := make(map[string]artifact.RunArtifact, len(artifacts))
	for _, item := range artifacts {
		existing, ok := out[item.ArtifactType]
		if !ok || item.CreatedAt.After(existing.CreatedAt) {
			out[item.ArtifactType] = item
		}
	}
	return out
}

func validateManifestArtifacts(manifest *runManifestArtifact, manifestStatus string, artifacts map[string]artifact.RunArtifact) (string, []string) {
	if manifestStatus == ManifestStatusMissing {
		return ArtifactValidationStatusMissing, []string{"artifact validation skipped because run_manifest is missing"}
	}
	if manifestStatus != ManifestStatusValid || manifest == nil {
		return ArtifactValidationStatusInvalid, []string{"artifact validation skipped because run_manifest is invalid"}
	}
	var warnings []string
	for artifactType, raw := range manifest.Artifacts {
		value, ok, err := manifestArtifactValue(raw)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("manifest artifact %s is invalid", artifactType))
			continue
		}
		if !ok || value == "" {
			continue
		}
		if _, exists := artifacts[artifactType]; !exists {
			warnings = append(warnings, fmt.Sprintf("manifest artifact %s is not archived", artifactType))
		}
	}
	if len(warnings) > 0 {
		return ArtifactValidationStatusInvalid, warnings
	}
	return ArtifactValidationStatusValid, nil
}

func (m *runManifestArtifact) validate() error {
	if m == nil {
		return errors.New("run_manifest is empty")
	}
	if strings.TrimSpace(m.RunID) == "" {
		return errors.New("run_manifest run_id is missing")
	}
	if strings.TrimSpace(m.Status) == "" {
		return errors.New("run_manifest status is missing")
	}
	if len(m.Artifacts) == 0 {
		return errors.New("run_manifest artifacts are missing")
	}
	for artifactType, raw := range m.Artifacts {
		value, ok, err := manifestArtifactValue(raw)
		if err != nil {
			return fmt.Errorf("run_manifest artifact %s must be a relative path or null", artifactType)
		}
		if !ok || value == "" {
			continue
		}
		if !safeManifestRelativePath(value) {
			return fmt.Errorf("run_manifest artifact %s has unsafe path", artifactType)
		}
	}
	return nil
}

func (m *runManifestArtifact) hasArtifact(artifactType string) bool {
	if m == nil {
		return false
	}
	value, ok, err := manifestArtifactValue(m.Artifacts[artifactType])
	return err == nil && ok && value != ""
}

func manifestArtifactValue(raw any) (string, bool, error) {
	if raw == nil {
		return "", false, nil
	}
	value, ok := raw.(string)
	if !ok {
		return "", false, fmt.Errorf("artifact path must be a string or null")
	}
	value = strings.TrimSpace(value)
	if value == "" {
		return "", false, nil
	}
	return value, true, nil
}

func safeManifestRelativePath(value string) bool {
	if strings.ContainsRune(value, 0) || filepath.IsAbs(value) {
		return false
	}
	clean := filepath.Clean(value)
	if clean == "." || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return false
	}
	return filepath.IsLocal(value)
}

func summaryFromRun(run FillRun) FillRunSummaryCounts {
	return FillRunSummaryCounts{
		TotalFields:        run.ProgressTotal,
		Answered:           run.AnsweredCount,
		PartialClue:        run.PartialClueCount,
		NotFound:           run.NotFoundCount,
		ConflictUnresolved: run.ConflictUnresolvedCount,
		WritebackAllowed:   run.WritebackAllowedCount,
		ReviewRequired:     run.ReviewRequiredCount,
		FailedFields:       run.FailedCount,
	}
}

func summaryFromManifest(manifest *runManifestArtifact) FillRunSummaryCounts {
	if manifest == nil {
		return FillRunSummaryCounts{}
	}
	return FillRunSummaryCounts{
		TotalFields:        intFromValue(manifest.Counts["total_fields"]),
		Answered:           intFromValue(manifest.Counts["answered"]),
		PartialClue:        intFromValue(manifest.Counts["partial_clue"]),
		NotFound:           intFromValue(manifest.Counts["not_found"]),
		ConflictUnresolved: intFromValue(manifest.Counts["conflict_unresolved"]),
		WritebackAllowed:   intFromValue(manifest.Counts["writeback_allowed"]),
		ReviewRequired:     intFromValue(manifest.Counts["review_required"]),
		FailedFields:       intFromValue(manifest.Counts["failed"]),
	}
}

func summaryFromMap(raw map[string]any) FillRunSummaryCounts {
	return FillRunSummaryCounts{
		TotalFields:        firstInt(raw, []string{"total_fields"}, []string{"field_count"}, []string{"total"}, []string{"fields_total"}, []string{"trace_summary", "total_fields"}),
		Answered:           firstInt(raw, []string{"answered"}, []string{"answered_count"}, []string{"raw_status_counts", "answered"}, []string{"answer_status_counts", "answered"}, []string{"trace_summary", "answered_count"}),
		PartialClue:        firstInt(raw, []string{"partial_clue"}, []string{"partial_clue_count"}, []string{"raw_status_counts", "partial_clue"}, []string{"answer_status_counts", "partial_clue"}, []string{"trace_summary", "partial_clue_count"}),
		NotFound:           firstInt(raw, []string{"not_found"}, []string{"not_found_count"}, []string{"raw_status_counts", "not_found"}, []string{"answer_status_counts", "not_found"}, []string{"trace_summary", "not_found_count"}),
		ConflictUnresolved: firstInt(raw, []string{"conflict_unresolved"}, []string{"conflict_unresolved_count"}, []string{"raw_status_counts", "conflict_unresolved"}, []string{"answer_status_counts", "conflict_unresolved"}, []string{"trace_summary", "conflict_unresolved_count"}),
		WritebackAllowed:   firstInt(raw, []string{"writeback_allowed"}, []string{"writeback_allowed_count"}, []string{"overlay_counts", "writeback_allowed"}, []string{"counts", "writeback_allowed"}),
		ReviewRequired:     firstInt(raw, []string{"review_required"}, []string{"review_required_count"}, []string{"overlay_counts", "review_required"}, []string{"counts", "review_required"}, []string{"trace_summary", "review_count"}),
		FailedFields:       firstInt(raw, []string{"failed_fields"}, []string{"failed_count"}, []string{"failed_count"}, []string{"failures"}, []string{"trace_summary", "failed_count"}, []string{"counts", "failed"}),
	}
}

func mergeSummaryCounts(primary FillRunSummaryCounts, fallback FillRunSummaryCounts) FillRunSummaryCounts {
	if primary.TotalFields == 0 {
		primary.TotalFields = fallback.TotalFields
	}
	if primary.Answered == 0 {
		primary.Answered = fallback.Answered
	}
	if primary.PartialClue == 0 {
		primary.PartialClue = fallback.PartialClue
	}
	if primary.NotFound == 0 {
		primary.NotFound = fallback.NotFound
	}
	if primary.ConflictUnresolved == 0 {
		primary.ConflictUnresolved = fallback.ConflictUnresolved
	}
	if primary.WritebackAllowed == 0 {
		primary.WritebackAllowed = fallback.WritebackAllowed
	}
	if primary.ReviewRequired == 0 {
		primary.ReviewRequired = fallback.ReviewRequired
	}
	if primary.FailedFields == 0 {
		primary.FailedFields = fallback.FailedFields
	}
	return primary
}

func firstInt(raw map[string]any, paths ...[]string) int {
	for _, path := range paths {
		value, ok := nestedValue(raw, path)
		if ok {
			return intFromValue(value)
		}
	}
	return 0
}

func nestedValue(raw map[string]any, path []string) (any, bool) {
	var current any = raw
	for _, part := range path {
		currentMap, ok := current.(map[string]any)
		if !ok {
			return nil, false
		}
		current, ok = currentMap[part]
		if !ok {
			return nil, false
		}
	}
	return current, true
}

func intFromValue(value any) int {
	switch v := value.(type) {
	case json.Number:
		if i, err := v.Int64(); err == nil {
			return int(i)
		}
		if f, err := v.Float64(); err == nil {
			return int(f)
		}
	case float64:
		return int(v)
	case float32:
		return int(v)
	case int:
		return v
	case int64:
		return int(v)
	case int32:
		return int(v)
	case string:
		i, _ := strconv.Atoi(strings.TrimSpace(v))
		return i
	}
	return 0
}

func downloadsForResult(result *resultContext) FillRunDownloads {
	terminal := isDownloadReadyStatus(result.run.Status)
	filled := terminal &&
		result.manifestStatus == ManifestStatusValid &&
		result.artifactValidationStatus == ArtifactValidationStatusValid &&
		result.manifest != nil &&
		result.manifest.hasArtifact(artifact.TypeFilledForm)
	_, filledArchived := result.artifactByType[artifact.TypeFilledForm]
	_, reviewItems := result.artifactByType[artifact.TypeReviewItems]
	_, writebackAudit := result.artifactByType[artifact.TypeWritebackAudit]
	return FillRunDownloads{
		FilledFormAvailable:     filled && filledArchived,
		ReviewItemsAvailable:    terminal && reviewItems,
		WritebackAuditAvailable: terminal && writebackAudit,
	}
}

func artifactDownloads(artifacts map[string]artifact.RunArtifact) FillRunArtifactDownloads {
	filled := artifactInfo(artifacts, artifact.TypeFilledForm, "filled_form.xlsx")
	reviewJSONL := artifactInfo(artifacts, artifact.TypeReviewItems, "review_items.jsonl")
	reviewCSV := FillRunArtifactInfo{Available: reviewJSONL.Available, Filename: "review_items.csv"}
	audit := artifactInfo(artifacts, artifact.TypeWritebackAudit, "writeback_audit.jsonl")
	summary := artifactInfo(artifacts, artifact.TypeSummary, "summary.json")
	return FillRunArtifactDownloads{
		FilledForm:     filled,
		ReviewItems:    reviewJSONL,
		ReviewItemsCSV: reviewCSV,
		WritebackAudit: audit,
		Summary:        summary,
	}
}

func artifactInfo(artifacts map[string]artifact.RunArtifact, artifactType string, fallbackFilename string) FillRunArtifactInfo {
	item, ok := artifacts[artifactType]
	if !ok {
		return FillRunArtifactInfo{Available: false, Filename: fallbackFilename}
	}
	filename := strings.TrimSpace(item.Filename)
	if filename == "" {
		filename = fallbackFilename
	}
	return FillRunArtifactInfo{Available: true, Filename: filename, Size: item.FileSize}
}

func ensureFillRunDownloadReady(run *FillRun) error {
	if run == nil {
		return httpx.NewAppError(httpx.CodeNotFound, "fill run not found", http.StatusNotFound, nil, nil)
	}
	if isDownloadReadyStatus(run.Status) {
		return nil
	}
	switch run.Status {
	case FillRunStatusCreated, FillRunStatusQueued, FillRunStatusRunning, FillRunStatusCancelRequested:
		return httpx.NewAppError(httpx.CodeConflict, "fill run is not completed yet", http.StatusConflict, map[string]string{"status": publicFillRunStatus(run.Status)}, nil)
	default:
		return httpx.NewAppError(httpx.CodeConflict, "fill run did not complete successfully", http.StatusConflict, map[string]string{"status": publicFillRunStatus(run.Status)}, nil)
	}
}

func isDownloadReadyStatus(status string) bool {
	return status == FillRunStatusSucceeded || status == FillRunStatusCompletedWithFailures
}

func publicFillRunStatus(status string) string {
	switch status {
	case FillRunStatusSucceeded:
		return "completed"
	case FillRunStatusCanceled:
		return "cancelled"
	default:
		return status
	}
}

func ReviewItemsJSONLToCSV(reader io.Reader, writer io.Writer) error {
	csvWriter := csv.NewWriter(writer)
	if err := csvWriter.Write([]string{
		"field_id",
		"row_index",
		"question_text",
		"answer_status",
		"answer_value",
		"risk_level",
		"review_required",
		"writeback_allowed",
		"reasons",
		"source_chunk_ids",
		"notes",
	}); err != nil {
		return err
	}
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 0, 64*1024), 10*1024*1024)
	var parsed int
	var bad int
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var row map[string]any
		decoder := json.NewDecoder(strings.NewReader(line))
		decoder.UseNumber()
		if err := decoder.Decode(&row); err != nil {
			bad++
			continue
		}
		parsed++
		if err := csvWriter.Write([]string{
			stringField(row, "field_id"),
			stringField(row, "row_index"),
			stringField(row, "question_text"),
			stringField(row, "answer_status"),
			firstStringField(row, "answer_value", "proposed_answer"),
			stringField(row, "risk_level"),
			stringField(row, "review_required"),
			stringField(row, "writeback_allowed"),
			joinListField(row, "reasons", "reason"),
			joinListField(row, "source_chunk_ids", "candidate_chunk_ids"),
			firstStringField(row, "notes", "review_comment", "suggested_action"),
		}); err != nil {
			return err
		}
	}
	if err := scanner.Err(); err != nil {
		return err
	}
	csvWriter.Flush()
	if err := csvWriter.Error(); err != nil {
		return err
	}
	if parsed == 0 && bad > 0 {
		return fmt.Errorf("all review_items JSONL rows are invalid")
	}
	return nil
}

func stringField(row map[string]any, key string) string {
	return valueToString(row[key])
}

func firstStringField(row map[string]any, keys ...string) string {
	for _, key := range keys {
		value := valueToString(row[key])
		if value != "" {
			return value
		}
	}
	return ""
}

func joinListField(row map[string]any, keys ...string) string {
	for _, key := range keys {
		value, ok := row[key]
		if !ok || value == nil {
			continue
		}
		switch typed := value.(type) {
		case []any:
			parts := make([]string, 0, len(typed))
			for _, item := range typed {
				if text := valueToString(item); text != "" {
					parts = append(parts, text)
				}
			}
			return strings.Join(parts, ";")
		default:
			if text := valueToString(value); text != "" {
				return text
			}
		}
	}
	return ""
}

func valueToString(value any) string {
	switch typed := value.(type) {
	case nil:
		return ""
	case string:
		return typed
	case json.Number:
		return typed.String()
	case bool:
		return strconv.FormatBool(typed)
	case float64:
		return strconv.FormatFloat(typed, 'f', -1, 64)
	case float32:
		return strconv.FormatFloat(float64(typed), 'f', -1, 32)
	case int:
		return strconv.Itoa(typed)
	case int64:
		return strconv.FormatInt(typed, 10)
	case []any:
		parts := make([]string, 0, len(typed))
		for _, item := range typed {
			if text := valueToString(item); text != "" {
				parts = append(parts, text)
			}
		}
		return strings.Join(parts, ";")
	default:
		data, err := json.Marshal(typed)
		if err != nil {
			return fmt.Sprint(typed)
		}
		return string(data)
	}
}
