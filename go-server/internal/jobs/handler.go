package jobs

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type UseCase interface {
	CreateJob(ctx context.Context, req CreateJobRequest, actor auth.Principal) (*Job, error)
	GetJob(ctx context.Context, jobID uuid.UUID, actor auth.Principal) (*Job, error)
	ListJobs(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int, actor auth.Principal) ([]Job, error)
	CancelJob(ctx context.Context, jobID uuid.UUID, actor auth.Principal) (*Job, error)
}

type Handler struct {
	service       UseCase
	enableNoopJob bool
}

func NewHandler(service UseCase, enableNoopJob bool) *Handler {
	return &Handler{service: service, enableNoopJob: enableNoopJob}
}

func (h *Handler) RegisterRoutes(r chi.Router) {
	r.Get("/jobs", h.List)
	r.Get("/jobs/{job_id}", h.Get)
	r.Post("/jobs/{job_id}/cancel", h.Cancel)
}

func (h *Handler) RegisterAdminRoutes(r chi.Router) {
	r.Post("/admin/noop-jobs", h.CreateNoop)
}

func (h *Handler) List(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	workspaceID, err := uuid.Parse(strings.TrimSpace(r.URL.Query().Get("workspace_id")))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "workspace_id is required", http.StatusBadRequest, nil, err))
		return
	}
	status := strings.TrimSpace(r.URL.Query().Get("status"))
	if status != "" && !ValidJobStatus(status) {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid job status", http.StatusBadRequest, map[string]string{"status": status}, nil))
		return
	}
	limit := parseIntQuery(r, "limit", 50)
	if limit > 200 {
		limit = 200
	}
	offset := parseIntQuery(r, "offset", 0)
	jobs, err := h.service.ListJobs(r.Context(), workspaceID, status, limit, offset, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, map[string]any{"jobs": jobs})
}

func (h *Handler) Get(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	jobID, err := jobIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	job, err := h.service.GetJob(r.Context(), jobID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, job)
}

func (h *Handler) Cancel(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	jobID, err := jobIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	job, err := h.service.CancelJob(r.Context(), jobID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, map[string]any{"job": job, "canceled": job.Status == JobStatusCanceled})
}

func (h *Handler) CreateNoop(w http.ResponseWriter, r *http.Request) {
	if !h.enableNoopJob {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeNotFound, "noop job endpoint is disabled", http.StatusNotFound, nil, nil))
		return
	}
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	var req NoopJobRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid JSON body", http.StatusBadRequest, nil, err))
		return
	}
	if req.WorkspaceID == uuid.Nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "workspace_id is required", http.StatusBadRequest, nil, nil))
		return
	}
	if req.SleepMS < 0 {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "sleep_ms must be non-negative", http.StatusBadRequest, nil, nil))
		return
	}
	job, err := h.service.CreateJob(r.Context(), CreateJobRequest{
		WorkspaceID:  req.WorkspaceID,
		JobType:      JobTypeNoop,
		ResourceType: ResourceTypeNoop,
		Payload:      map[string]any{"sleep_ms": req.SleepMS},
	}, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, job)
}

func jobIDFromRequest(r *http.Request) (uuid.UUID, error) {
	jobID, err := uuid.Parse(chi.URLParam(r, "job_id"))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid job id", http.StatusBadRequest, nil, err)
	}
	return jobID, nil
}

func parseIntQuery(r *http.Request, name string, fallback int) int {
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

func unauthorized() error {
	return httpx.NewAppError(httpx.CodeUnauthorized, "missing authenticated user", http.StatusUnauthorized, nil, nil)
}
