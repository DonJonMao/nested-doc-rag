package review

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type UseCase interface {
	ListByRun(ctx context.Context, runID uuid.UUID, filter ReviewFilter, actor auth.Principal) ([]ReviewItem, ReviewCounts, error)
	CountByRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (ReviewCounts, error)
	Get(ctx context.Context, itemID uuid.UUID, actor auth.Principal) (*ReviewItem, error)
	Approve(ctx context.Context, itemID uuid.UUID, comment string, actor auth.Principal) (*ReviewItem, error)
	Reject(ctx context.Context, itemID uuid.UUID, reason string, actor auth.Principal) (*ReviewItem, error)
	Edit(ctx context.Context, itemID uuid.UUID, editedAnswer string, comment string, actor auth.Principal) (*ReviewItem, error)
	Ignore(ctx context.Context, itemID uuid.UUID, comment string, actor auth.Principal) (*ReviewItem, error)
	Reopen(ctx context.Context, itemID uuid.UUID, comment string, actor auth.Principal) (*ReviewItem, error)
}

type FillRunResultUseCase interface {
	GetFillRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*form.FillRun, error)
	GetFillRunArtifacts(ctx context.Context, runID uuid.UUID, actor auth.Principal) ([]artifact.RunArtifact, error)
}

type Handler struct {
	reviews UseCase
	runs    FillRunResultUseCase
}

func NewHandler(reviews UseCase, runs FillRunResultUseCase) *Handler {
	return &Handler{reviews: reviews, runs: runs}
}

func (h *Handler) RegisterRoutes(r chi.Router) {
	r.Get("/fill-runs/{run_id}/review-items", h.ListReviewItems)
	r.Get("/fill-runs/{run_id}/review-items/export", h.ExportReviewItems)
	r.Get("/fill-runs/{run_id}/result", h.GetFillRunResult)
	r.Get("/review-items/{item_id}", h.GetReviewItem)
	r.Post("/review-items/{item_id}/approve", h.Approve)
	r.Post("/review-items/{item_id}/reject", h.Reject)
	r.Post("/review-items/{item_id}/edit", h.Edit)
	r.Post("/review-items/{item_id}/ignore", h.Ignore)
	r.Post("/review-items/{item_id}/reopen", h.Reopen)
}

func (h *Handler) ListReviewItems(w http.ResponseWriter, r *http.Request) {
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
	items, counts, err := h.reviews.ListByRun(r.Context(), runID, filterFromQuery(r), actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, ReviewItemListResponse{Items: items, Counts: counts})
}

func (h *Handler) GetReviewItem(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	itemID, err := itemIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	item, err := h.reviews.Get(r.Context(), itemID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, item)
}

func (h *Handler) Approve(w http.ResponseWriter, r *http.Request) {
	h.handleAction(w, r, ReviewActionApprove)
}

func (h *Handler) Reject(w http.ResponseWriter, r *http.Request) {
	h.handleAction(w, r, ReviewActionReject)
}

func (h *Handler) Ignore(w http.ResponseWriter, r *http.Request) {
	h.handleAction(w, r, ReviewActionIgnore)
}

func (h *Handler) Reopen(w http.ResponseWriter, r *http.Request) {
	h.handleAction(w, r, ReviewActionReopen)
}

func (h *Handler) Edit(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	itemID, err := itemIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	var req ReviewEditRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid JSON body", http.StatusBadRequest, nil, err))
		return
	}
	item, err := h.reviews.Edit(r.Context(), itemID, req.EditedAnswer, req.Comment, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, item)
}

func (h *Handler) ExportReviewItems(w http.ResponseWriter, r *http.Request) {
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
	filter := filterFromQuery(r)
	filter.Limit = 200
	items, _, err := h.reviews.ListByRun(r.Context(), runID, filter, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	format := strings.TrimSpace(r.URL.Query().Get("format"))
	if format == "" {
		format = "json"
	}
	var data []byte
	var contentType string
	var filename string
	switch format {
	case "json":
		data, err = ExportJSON(items)
		contentType = "application/json; charset=utf-8"
		filename = "review_items.json"
	case "csv":
		data, err = ExportCSV(items)
		contentType = "text/csv; charset=utf-8"
		filename = "review_items.csv"
	default:
		err = httpx.NewAppError(httpx.CodeInvalidArgument, "invalid export format", http.StatusBadRequest, map[string]string{"format": format}, nil)
	}
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	w.Header().Set("Content-Disposition", contentDisposition(filename))
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}

func (h *Handler) GetFillRunResult(w http.ResponseWriter, r *http.Request) {
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
	run, err := h.runs.GetFillRun(r.Context(), runID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	artifacts, err := h.runs.GetFillRunArtifacts(r.Context(), runID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	counts, err := h.reviews.CountByRun(r.Context(), runID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	base := "/api/v1/fill-runs/" + run.ID.String() + "/download/"
	httpx.WriteOK(w, r, FillRunResultResponse{
		Run:          run,
		Artifacts:    artifacts,
		ReviewCounts: counts,
		Downloads: map[string]string{
			"filled_form":  base + "filled-form",
			"run_summary":  base + "run-summary",
			"review_items": base + "review-items",
			"trace":        base + "trace",
		},
	})
}

func (h *Handler) handleAction(w http.ResponseWriter, r *http.Request, action string) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	itemID, err := itemIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	var req ReviewActionRequest
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&req)
	}
	var item *ReviewItem
	switch action {
	case ReviewActionApprove:
		item, err = h.reviews.Approve(r.Context(), itemID, req.Comment, actor)
	case ReviewActionReject:
		reason := req.Reason
		if reason == "" {
			reason = req.Comment
		}
		item, err = h.reviews.Reject(r.Context(), itemID, reason, actor)
	case ReviewActionIgnore:
		item, err = h.reviews.Ignore(r.Context(), itemID, req.Comment, actor)
	case ReviewActionReopen:
		item, err = h.reviews.Reopen(r.Context(), itemID, req.Comment, actor)
	default:
		err = httpx.NewAppError(httpx.CodeInvalidArgument, "invalid review action", http.StatusBadRequest, nil, nil)
	}
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, item)
}

func filterFromQuery(r *http.Request) ReviewFilter {
	filter := ReviewFilter{
		Status:    strings.TrimSpace(r.URL.Query().Get("status")),
		RiskLevel: strings.TrimSpace(r.URL.Query().Get("risk_level")),
		Limit:     limitFromQuery(r),
		Offset:    offsetFromQuery(r),
	}
	if value := strings.TrimSpace(r.URL.Query().Get("review_required")); value != "" {
		parsed := value == "true" || value == "1"
		filter.ReviewRequired = &parsed
	}
	if value := strings.TrimSpace(r.URL.Query().Get("writeback_allowed")); value != "" {
		parsed := value == "true" || value == "1"
		filter.WritebackAllowed = &parsed
	}
	return filter
}

func runIDFromRequest(r *http.Request) (uuid.UUID, error) {
	runID, err := uuid.Parse(chi.URLParam(r, "run_id"))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid fill run id", http.StatusBadRequest, nil, err)
	}
	return runID, nil
}

func itemIDFromRequest(r *http.Request) (uuid.UUID, error) {
	itemID, err := uuid.Parse(chi.URLParam(r, "item_id"))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid review item id", http.StatusBadRequest, nil, err)
	}
	return itemID, nil
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

func contentDisposition(filename string) string {
	return fmt.Sprintf("attachment; filename=%q; filename*=UTF-8''%s", filename, url.PathEscape(filename))
}

func unauthorized() error {
	return httpx.NewAppError(httpx.CodeUnauthorized, "missing authenticated user", http.StatusUnauthorized, nil, nil)
}
