package form

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type FormUseCase interface {
	UploadForm(ctx context.Context, req UploadFormRequest, actor auth.Principal) (*FormFile, error)
	GetForm(ctx context.Context, formID uuid.UUID, actor auth.Principal) (*FormFile, error)
	ListForms(ctx context.Context, workspaceID uuid.UUID, limit int, offset int, actor auth.Principal) ([]FormFile, error)
}

type FillRunUseCase interface {
	CreateFillRun(ctx context.Context, req CreateFillRunRequest, actor auth.Principal) (*FillRun, error)
	CreateSimpleFillRun(ctx context.Context, req CreateSimpleFillRunRequest, actor auth.Principal) (*FillRun, error)
	GetFillRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*FillRun, error)
	ListFillRuns(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int, mine bool, actor auth.Principal) ([]FillRun, error)
	GetFillRunDetail(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*FillRunDetail, error)
	ListFillRunSummaries(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int, mine bool, actor auth.Principal) ([]FillRunListItem, error)
	CancelFillRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*FillRun, error)
	GetFillRunArtifacts(ctx context.Context, runID uuid.UUID, actor auth.Principal) ([]artifact.RunArtifact, error)
	GetDownloadArtifactByType(ctx context.Context, runID uuid.UUID, artifactType string, actor auth.Principal) (*artifact.DownloadResult, error)
	DownloadFilledForm(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error)
	DownloadReviewItems(ctx context.Context, runID uuid.UUID, format string, actor auth.Principal) (*artifact.DownloadResult, error)
	DownloadWritebackAudit(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error)
	DownloadSummary(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error)
}

type Handler struct {
	forms FormUseCase
	runs  FillRunUseCase
}

func NewHandler(forms FormUseCase, runs FillRunUseCase) *Handler {
	return &Handler{forms: forms, runs: runs}
}

func (h *Handler) RegisterRoutes(r chi.Router) {
	r.Post("/forms", h.UploadForm)
	r.Get("/forms", h.ListForms)
	r.Get("/forms/{form_id}", h.GetForm)
	r.Post("/fill-runs/simple", h.CreateSimpleFillRun)
	r.Post("/fill-runs", h.CreateFillRun)
	r.Get("/fill-runs", h.ListFillRuns)
	r.Get("/fill-runs/{run_id}", h.GetFillRun)
	r.Post("/fill-runs/{run_id}/cancel", h.CancelFillRun)
	r.Get("/fill-runs/{run_id}/artifacts", h.ListFillRunArtifacts)
	r.Get("/fill-runs/{run_id}/download/{artifact_kind}", h.DownloadFillRunArtifact)
	r.Get("/fill-runs/{run_id}/downloads/filled-form", h.DownloadFilledForm)
	r.Get("/fill-runs/{run_id}/downloads/review-items", h.DownloadReviewItems)
	r.Get("/fill-runs/{run_id}/downloads/writeback-audit", h.DownloadWritebackAudit)
	r.Get("/fill-runs/{run_id}/downloads/summary", h.DownloadSummary)
}

func (h *Handler) UploadForm(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	if err := r.ParseMultipartForm(32 << 20); err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid multipart form", http.StatusBadRequest, nil, err))
		return
	}
	workspaceID, err := uuid.Parse(strings.TrimSpace(r.FormValue("workspace_id")))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "workspace_id is required", http.StatusBadRequest, nil, err))
		return
	}
	src, header, err := r.FormFile("file")
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "file is required", http.StatusBadRequest, nil, err))
		return
	}
	defer src.Close()
	formFile, err := h.forms.UploadForm(r.Context(), UploadFormRequest{
		WorkspaceID:      workspaceID,
		OriginalFilename: header.Filename,
		Size:             header.Size,
		MIMEType:         header.Header.Get("Content-Type"),
		Reader:           src,
	}, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, formFile)
}

func (h *Handler) ListForms(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	workspaceID, err := workspaceIDFromQuery(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	forms, err := h.forms.ListForms(r.Context(), workspaceID, limitFromQuery(r), offsetFromQuery(r), actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, FormListResponse{Forms: forms})
}

func (h *Handler) GetForm(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	formID, err := uuid.Parse(chi.URLParam(r, "form_id"))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid form id", http.StatusBadRequest, nil, err))
		return
	}
	formFile, err := h.forms.GetForm(r.Context(), formID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, formFile)
}

func (h *Handler) CreateFillRun(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	var req CreateFillRunRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid JSON body", http.StatusBadRequest, nil, err))
		return
	}
	run, err := h.runs.CreateFillRun(r.Context(), req, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, run)
}

func (h *Handler) CreateSimpleFillRun(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	var req CreateSimpleFillRunRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid JSON body", http.StatusBadRequest, nil, err))
		return
	}
	run, err := h.runs.CreateSimpleFillRun(r.Context(), req, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, run)
}

func (h *Handler) ListFillRuns(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	workspaceID, err := optionalWorkspaceIDFromQuery(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	status := strings.TrimSpace(r.URL.Query().Get("status"))
	if status != "" && !ValidFillRunStatus(status) {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid fill run status", http.StatusBadRequest, nil, nil))
		return
	}
	runs, err := h.runs.ListFillRunSummaries(r.Context(), workspaceID, status, limitFromQuery(r), offsetFromQuery(r), boolFromQuery(r, "mine"), actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	if runs == nil {
		runs = []FillRunListItem{}
	}
	httpx.WriteOK(w, r, FillRunListResponse{FillRuns: runs})
}

func (h *Handler) GetFillRun(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	runID, err := runIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	run, err := h.runs.GetFillRunDetail(r.Context(), runID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, run)
}

func (h *Handler) CancelFillRun(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	runID, err := runIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	run, err := h.runs.CancelFillRun(r.Context(), runID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, CancelFillRunResponse{FillRun: run, Canceled: run.Status == FillRunStatusCanceled})
}

func (h *Handler) ListFillRunArtifacts(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	runID, err := runIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	artifacts, err := h.runs.GetFillRunArtifacts(r.Context(), runID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, FillRunArtifactListResponse{Artifacts: artifacts})
}

func (h *Handler) DownloadFillRunArtifact(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	runID, err := runIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	artifactType, err := artifactTypeFromKind(chi.URLParam(r, "artifact_kind"))
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.runs.GetDownloadArtifactByType(r.Context(), runID, artifactType, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	streamDownload(w, r, result)
}

func (h *Handler) DownloadFilledForm(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	runID, err := runIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.runs.DownloadFilledForm(r.Context(), runID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	streamDownload(w, r, result)
}

func (h *Handler) DownloadReviewItems(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	runID, err := runIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.runs.DownloadReviewItems(r.Context(), runID, r.URL.Query().Get("format"), actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	streamDownload(w, r, result)
}

func (h *Handler) DownloadWritebackAudit(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	runID, err := runIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.runs.DownloadWritebackAudit(r.Context(), runID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	streamDownload(w, r, result)
}

func (h *Handler) DownloadSummary(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	runID, err := runIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.runs.DownloadSummary(r.Context(), runID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	streamDownload(w, r, result)
}

func streamDownload(w http.ResponseWriter, r *http.Request, result *artifact.DownloadResult) {
	if result.PresignedURL != "" {
		httpx.WriteOK(w, r, map[string]string{"url": result.PresignedURL})
		return
	}
	defer result.Reader.Close()
	if result.ContentType != "" {
		w.Header().Set("Content-Type", result.ContentType)
	}
	if result.ContentLength >= 0 {
		w.Header().Set("Content-Length", strconv.FormatInt(result.ContentLength, 10))
	}
	w.Header().Set("Content-Disposition", contentDisposition(result.Filename))
	w.WriteHeader(http.StatusOK)
	_, _ = io.Copy(w, result.Reader)
}

func workspaceIDFromQuery(r *http.Request) (uuid.UUID, error) {
	workspaceID, err := uuid.Parse(strings.TrimSpace(r.URL.Query().Get("workspace_id")))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "workspace_id is required", http.StatusBadRequest, nil, err)
	}
	return workspaceID, nil
}

func optionalWorkspaceIDFromQuery(r *http.Request) (uuid.UUID, error) {
	raw := strings.TrimSpace(r.URL.Query().Get("workspace_id"))
	if raw == "" {
		return uuid.Nil, nil
	}
	workspaceID, err := uuid.Parse(raw)
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid workspace_id", http.StatusBadRequest, nil, err)
	}
	return workspaceID, nil
}

func runIDFromRequest(r *http.Request) (uuid.UUID, error) {
	runID, err := uuid.Parse(chi.URLParam(r, "run_id"))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid fill run id", http.StatusBadRequest, nil, err)
	}
	return runID, nil
}

func limitFromQuery(r *http.Request) int {
	limit := intFromQuery(r, "limit", 50)
	if limit > 200 {
		return 200
	}
	return limit
}

func offsetFromQuery(r *http.Request) int {
	return intFromQuery(r, "offset", 0)
}

func intFromQuery(r *http.Request, name string, fallback int) int {
	value := strings.TrimSpace(r.URL.Query().Get(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed < 0 {
		return fallback
	}
	return parsed
}

func boolFromQuery(r *http.Request, name string) bool {
	value := strings.ToLower(strings.TrimSpace(r.URL.Query().Get(name)))
	return value == "true" || value == "1" || value == "yes"
}

func artifactTypeFromKind(kind string) (string, error) {
	switch kind {
	case "filled-form":
		return artifact.TypeFilledForm, nil
	case "run-summary":
		return artifact.TypeRunSummary, nil
	case "review-items":
		return artifact.TypeReviewItems, nil
	case "trace":
		return artifact.TypeTrace, nil
	default:
		return "", httpx.NewAppError(httpx.CodeInvalidArgument, "invalid artifact kind", http.StatusBadRequest, map[string]string{"artifact_kind": kind}, nil)
	}
}

func contentDisposition(filename string) string {
	filename = filepkg.SanitizeFilename(filename)
	return fmt.Sprintf("attachment; filename=%q; filename*=UTF-8''%s", filename, url.PathEscape(filename))
}

func unauthorized() error {
	return httpx.NewAppError(httpx.CodeUnauthorized, "missing authenticated user", http.StatusUnauthorized, nil, nil)
}
