package artifact

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type UseCase interface {
	GetArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*RunArtifact, error)
	ListRunArtifacts(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, actor auth.Principal) ([]RunArtifact, error)
	DownloadArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*DownloadResult, error)
}

type Handler struct {
	service UseCase
}

func NewHandler(service UseCase) *Handler {
	return &Handler{service: service}
}

func (h *Handler) RegisterRoutes(r chi.Router) {
	r.Get("/artifacts/{artifact_id}", h.Get)
	r.Get("/artifacts/{artifact_id}/download", h.Download)
	r.Get("/runs/{run_id}/artifacts", h.ListRunArtifacts)
}

func (h *Handler) Get(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	artifactID, err := artifactIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.service.GetArtifact(r.Context(), artifactID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, result)
}

func (h *Handler) Download(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	artifactID, err := artifactIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.service.DownloadArtifact(r.Context(), artifactID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
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

func (h *Handler) ListRunArtifacts(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	runID, err := uuid.Parse(chi.URLParam(r, "run_id"))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid run id", http.StatusBadRequest, nil, err))
		return
	}
	workspaceID, err := uuid.Parse(strings.TrimSpace(r.URL.Query().Get("workspace_id")))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "workspace_id is required", http.StatusBadRequest, nil, err))
		return
	}
	result, err := h.service.ListRunArtifacts(r.Context(), workspaceID, runID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, result)
}

func artifactIDFromRequest(r *http.Request) (uuid.UUID, error) {
	artifactID, err := uuid.Parse(chi.URLParam(r, "artifact_id"))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid artifact id", http.StatusBadRequest, nil, err)
	}
	return artifactID, nil
}

func contentDisposition(filename string) string {
	filename = filepkg.SanitizeFilename(filename)
	return fmt.Sprintf("attachment; filename=%q; filename*=UTF-8''%s", filename, url.PathEscape(filename))
}

func unauthorized() error {
	return httpx.NewAppError(httpx.CodeUnauthorized, "missing authenticated user", http.StatusUnauthorized, nil, nil)
}
