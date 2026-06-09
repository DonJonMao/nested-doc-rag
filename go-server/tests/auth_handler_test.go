package tests

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestAuthHandlerLoginSuccess(t *testing.T) {
	handler := auth.NewHandler(fakeAuthUseCase{loginResult: &auth.LoginResult{AccessToken: "access", RefreshToken: "refresh"}})
	router := authHandlerRouter(t, handler, nil)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", strings.NewReader(`{"username":"admin","password":"password123"}`))
	req.Header.Set("Content-Type", "application/json")

	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.Contains(t, rec.Body.String(), "access")
}

func TestAuthHandlerLoginInvalidReturnsUnauthorized(t *testing.T) {
	err := httpx.NewAppError(httpx.CodeUnauthorized, "invalid username or password", http.StatusUnauthorized, nil, nil)
	handler := auth.NewHandler(fakeAuthUseCase{loginErr: err})
	router := authHandlerRouter(t, handler, nil)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", strings.NewReader(`{"username":"admin","password":"bad"}`))
	req.Header.Set("Content-Type", "application/json")

	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusUnauthorized, rec.Code)
	require.Contains(t, rec.Body.String(), httpx.CodeUnauthorized)
}

func TestAuthHandlerMeRequiresToken(t *testing.T) {
	handler := auth.NewHandler(fakeAuthUseCase{})
	manager, err := auth.NewTokenManager("test-secret", time.Minute)
	require.NoError(t, err)
	router := authHandlerRouter(t, handler, manager)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/auth/me", nil)

	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusUnauthorized, rec.Code)
}

type fakeAuthUseCase struct {
	loginResult   *auth.LoginResult
	loginErr      error
	refreshResult *auth.LoginResult
	refreshErr    error
	logoutErr     error
	meResult      *auth.MeResult
	meErr         error
}

func (f fakeAuthUseCase) Login(ctx context.Context, username string, password string, ip string, userAgent string) (*auth.LoginResult, error) {
	return f.loginResult, f.loginErr
}

func (f fakeAuthUseCase) Refresh(ctx context.Context, refreshToken string) (*auth.LoginResult, error) {
	return f.refreshResult, f.refreshErr
}

func (f fakeAuthUseCase) Logout(ctx context.Context, refreshToken string) error {
	return f.logoutErr
}

func (f fakeAuthUseCase) Me(ctx context.Context, userID uuid.UUID) (*auth.MeResult, error) {
	if f.meResult != nil || f.meErr != nil {
		return f.meResult, f.meErr
	}
	return &auth.MeResult{User: auth.UserView{ID: userID, Username: "admin", Roles: []string{auth.RoleAdmin}}}, nil
}

func authHandlerRouter(t *testing.T, handler *auth.Handler, manager *auth.TokenManager) http.Handler {
	t.Helper()
	router := chi.NewRouter()
	router.Use(middleware.RequestID)
	router.Route("/api/v1", func(api chi.Router) {
		handler.RegisterPublicRoutes(api)
		if manager != nil {
			api.Group(func(protected chi.Router) {
				protected.Use(middleware.Auth(manager))
				handler.RegisterProtectedRoutes(protected)
			})
		}
	})
	return router
}
