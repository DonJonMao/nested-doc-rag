package file

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type UseCase interface {
	Upload(ctx context.Context, req UploadFileRequest, actor auth.Principal) (*File, error)
	Get(ctx context.Context, fileID uuid.UUID, actor auth.Principal) (*File, error)
	List(ctx context.Context, workspaceID uuid.UUID, category string, limit int, offset int, actor auth.Principal) ([]File, error)
	Download(ctx context.Context, fileID uuid.UUID, actor auth.Principal) (*DownloadResult, error)
	Delete(ctx context.Context, fileID uuid.UUID, actor auth.Principal) error
}

type Handler struct {
	service UseCase
}

func NewHandler(service UseCase) *Handler {
	return &Handler{service: service}
}

func (h *Handler) RegisterRoutes(r chi.Router) {
	r.Post("/files", h.Upload)
	r.Get("/files", h.List)
	r.Get("/files/{file_id}", h.Get)
	r.Get("/files/{file_id}/download", h.Download)
	r.Delete("/files/{file_id}", h.Delete)
}

func (h *Handler) Upload(w http.ResponseWriter, r *http.Request) {
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
	category := strings.TrimSpace(r.FormValue("file_category"))
	src, header, err := r.FormFile("file")
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "file is required", http.StatusBadRequest, nil, err))
		return
	}
	defer src.Close()
	mimeType := header.Header.Get("Content-Type")
	result, err := h.service.Upload(r.Context(), UploadFileRequest{
		WorkspaceID:      workspaceID,
		OriginalFilename: header.Filename,
		Size:             header.Size,
		MIMEType:         mimeType,
		Category:         category,
		Reader:           src,
	}, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, result)
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
	limit := parseIntQuery(r, "limit", 50)
	if limit > 200 {
		limit = 200
	}
	offset := parseIntQuery(r, "offset", 0)
	files, err := h.service.List(r.Context(), workspaceID, strings.TrimSpace(r.URL.Query().Get("file_category")), limit, offset, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, files)
}

func (h *Handler) Get(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	fileID, err := fileIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.service.Get(r.Context(), fileID, actor)
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
	fileID, err := fileIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.service.Download(r.Context(), fileID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
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

func (h *Handler) Delete(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	fileID, err := fileIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	if err := h.service.Delete(r.Context(), fileID, actor); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, map[string]bool{"deleted": true})
}

func fileIDFromRequest(r *http.Request) (uuid.UUID, error) {
	fileID, err := uuid.Parse(chi.URLParam(r, "file_id"))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid file id", http.StatusBadRequest, nil, err)
	}
	return fileID, nil
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

func contentDisposition(filename string) string {
	filename = SanitizeFilename(filename)
	return fmt.Sprintf("attachment; filename=%q; filename*=UTF-8''%s", filename, url.PathEscape(filename))
}

func unauthorized() error {
	return httpx.NewAppError(httpx.CodeUnauthorized, "missing authenticated user", http.StatusUnauthorized, nil, nil)
}
