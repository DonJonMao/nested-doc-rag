package workspace

import (
	"context"
	"encoding/json"
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

type UseCase interface {
	CreateWorkspace(ctx context.Context, req CreateWorkspaceRequest, actor auth.Principal) (*Workspace, error)
	ListMyWorkspaces(ctx context.Context, actor auth.Principal) ([]Workspace, error)
	GetWorkspace(ctx context.Context, id uuid.UUID, actor auth.Principal) (*Workspace, error)
	AddMember(ctx context.Context, workspaceID uuid.UUID, userID uuid.UUID, role string, actor auth.Principal) error
	ListMembers(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) ([]WorkspaceMemberView, error)
}

type Handler struct {
	service UseCase
}

func NewHandler(service UseCase) *Handler {
	return &Handler{service: service}
}

func (h *Handler) RegisterRoutes(r chi.Router) {
	r.Post("/workspaces", h.Create)
	r.Get("/workspaces", h.List)
	r.Get("/workspaces/{workspace_id}", h.Get)
	r.Get("/workspaces/{workspace_id}/members", h.ListMembers)
	r.Post("/workspaces/{workspace_id}/members", h.AddMember)
}

func (h *Handler) Create(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	var req CreateWorkspaceRequest
	if err := decodeJSON(r, &req); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.service.CreateWorkspace(r.Context(), req, actor)
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
	result, err := h.service.ListMyWorkspaces(r.Context(), actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, result)
}

func (h *Handler) Get(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	workspaceID, err := workspaceIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.service.GetWorkspace(r.Context(), workspaceID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, result)
}

func (h *Handler) ListMembers(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	workspaceID, err := workspaceIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.service.ListMembers(r.Context(), workspaceID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, result)
}

func (h *Handler) AddMember(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	workspaceID, err := workspaceIDFromRequest(r)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	var req AddMemberRequest
	if err := decodeJSON(r, &req); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	if err := h.service.AddMember(r.Context(), workspaceID, req.UserID, req.Role, actor); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, map[string]bool{"added": true})
}

func workspaceIDFromRequest(r *http.Request) (uuid.UUID, error) {
	workspaceID, err := uuid.Parse(chi.URLParam(r, "workspace_id"))
	if err != nil {
		return uuid.Nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid workspace id", http.StatusBadRequest, nil, err)
	}
	return workspaceID, nil
}

func decodeJSON(r *http.Request, dst any) error {
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(dst); err != nil {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "invalid request body", http.StatusBadRequest, nil, err)
	}
	return nil
}

func unauthorized() error {
	return httpx.NewAppError(httpx.CodeUnauthorized, "missing authenticated user", http.StatusUnauthorized, nil, nil)
}
