package tests

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestRequireRolesAdminPasses(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}))
	rec := httptest.NewRecorder()

	middleware.RequireRoles(auth.RoleAdmin)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})).ServeHTTP(rec, req)

	require.Equal(t, http.StatusNoContent, rec.Code)
}

func TestRequireRolesMissingRoleForbidden(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req = req.WithContext(auth.ContextWithPrincipal(req.Context(), auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleViewer}}))
	rec := httptest.NewRecorder()

	middleware.RequireRoles(auth.RoleAdmin)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})).ServeHTTP(rec, req)

	require.Equal(t, http.StatusForbidden, rec.Code)
}

func TestRequireWorkspaceRole(t *testing.T) {
	workspaceID := uuid.New()
	userID := uuid.New()
	reader := fakeWorkspaceRoleReader{role: "owner"}
	router := chi.NewRouter()
	router.With(
		func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				ctx := auth.ContextWithPrincipal(r.Context(), auth.Principal{UserID: userID, Roles: []string{auth.RoleOperator}})
				next.ServeHTTP(w, r.WithContext(ctx))
			})
		},
		middleware.RequireWorkspaceRole(reader, "workspace_id", "owner"),
	).Get("/workspaces/{workspace_id}", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/workspaces/"+workspaceID.String(), nil)

	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusNoContent, rec.Code)
}

type fakeWorkspaceRoleReader struct {
	role string
	err  error
}

func (f fakeWorkspaceRoleReader) GetMemberRole(ctx context.Context, workspaceID uuid.UUID, userID uuid.UUID) (string, error) {
	return f.role, f.err
}
