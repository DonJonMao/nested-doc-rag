package user

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
	CreateUser(ctx context.Context, req CreateUserRequest, actor auth.Principal) (*auth.UserView, error)
	GetUser(ctx context.Context, id uuid.UUID, actor auth.Principal) (*auth.UserView, error)
	ListUsers(ctx context.Context, actor auth.Principal) ([]auth.UserView, error)
	SetUserStatus(ctx context.Context, id uuid.UUID, status string, actor auth.Principal) error
	AssignRole(ctx context.Context, userID uuid.UUID, roleName string, actor auth.Principal) error
}

type Handler struct {
	service UseCase
}

func NewHandler(service UseCase) *Handler {
	return &Handler{service: service}
}

func (h *Handler) RegisterRoutes(r chi.Router) {
	r.Post("/users", h.Create)
	r.Get("/users", h.List)
	r.Get("/users/{user_id}", h.Get)
	r.Patch("/users/{user_id}/status", h.SetStatus)
	r.Post("/users/{user_id}/roles", h.AssignRole)
}

func (h *Handler) Create(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	var req CreateUserRequest
	if err := decodeJSON(r, &req); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	result, err := h.service.CreateUser(r.Context(), req, actor)
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
	result, err := h.service.ListUsers(r.Context(), actor)
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
	userID, err := uuid.Parse(chi.URLParam(r, "user_id"))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid user id", http.StatusBadRequest, nil, err))
		return
	}
	result, err := h.service.GetUser(r.Context(), userID, actor)
	if err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, result)
}

func (h *Handler) SetStatus(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	userID, err := uuid.Parse(chi.URLParam(r, "user_id"))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid user id", http.StatusBadRequest, nil, err))
		return
	}
	var req SetStatusRequest
	if err := decodeJSON(r, &req); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	if err := h.service.SetUserStatus(r.Context(), userID, req.Status, actor); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, map[string]bool{"updated": true})
}

func (h *Handler) AssignRole(w http.ResponseWriter, r *http.Request) {
	actor, ok := auth.PrincipalFromContext(r.Context())
	if !ok {
		httpx.WriteError(w, r, unauthorized())
		return
	}
	userID, err := uuid.Parse(chi.URLParam(r, "user_id"))
	if err != nil {
		httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid user id", http.StatusBadRequest, nil, err))
		return
	}
	var req AssignRoleRequest
	if err := decodeJSON(r, &req); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	if err := h.service.AssignRole(r.Context(), userID, req.Role, actor); err != nil {
		httpx.WriteError(w, r, err)
		return
	}
	httpx.WriteOK(w, r, map[string]bool{"assigned": true})
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
