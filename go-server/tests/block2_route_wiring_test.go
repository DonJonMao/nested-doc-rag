package tests

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestBlock2RoutesRequireAuth(t *testing.T) {
	router := block2RouteWiringRouter(t)
	cases := []struct {
		method string
		path   string
	}{
		{http.MethodGet, "/api/v1/files?workspace_id=" + uuid.NewString()},
		{http.MethodGet, "/api/v1/artifacts/" + uuid.NewString()},
		{http.MethodGet, "/api/v1/runs/" + uuid.NewString() + "/artifacts?workspace_id=" + uuid.NewString()},
	}

	for _, tc := range cases {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(tc.method, tc.path, nil)

		router.ServeHTTP(rec, req)

		require.Equal(t, http.StatusUnauthorized, rec.Code, tc.path)
		require.Contains(t, rec.Body.String(), httpx.CodeUnauthorized)
	}
}

func TestBlock2RoutesExistWithAuth(t *testing.T) {
	router := block2RouteWiringRouter(t)
	token := block2RouteToken(t)
	cases := []struct {
		method string
		path   string
	}{
		{http.MethodGet, "/api/v1/files?workspace_id=" + uuid.NewString()},
		{http.MethodGet, "/api/v1/artifacts/" + uuid.NewString()},
		{http.MethodGet, "/api/v1/runs/" + uuid.NewString() + "/artifacts?workspace_id=" + uuid.NewString()},
	}

	for _, tc := range cases {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(tc.method, tc.path, nil)
		req.Header.Set("Authorization", "Bearer "+token)

		router.ServeHTTP(rec, req)

		require.NotEqual(t, http.StatusNotFound, rec.Code, tc.path)
	}
}

func block2RouteWiringRouter(t *testing.T) http.Handler {
	t.Helper()
	manager, err := auth.NewTokenManager("route-secret", time.Minute)
	require.NoError(t, err)
	router := chi.NewRouter()
	router.Use(middleware.RequestID)
	router.Route("/api/v1", func(api chi.Router) {
		api.Group(func(protected chi.Router) {
			protected.Use(middleware.Auth(manager))
			filepkg.NewHandler(&fakeFileUseCase{}).RegisterRoutes(protected)
			artifact.NewHandler(&fakeArtifactUseCase{
				artifact:  &artifact.RunArtifact{ID: uuid.New()},
				artifacts: []artifact.RunArtifact{},
			}).RegisterRoutes(protected)
		})
	})
	return router
}

func block2RouteToken(t *testing.T) string {
	t.Helper()
	manager, err := auth.NewTokenManager("route-secret", time.Minute)
	require.NoError(t, err)
	token, _, err := manager.GenerateAccessToken(auth.User{ID: uuid.New(), Username: "route-user"}, []string{auth.RoleOperator})
	require.NoError(t, err)
	return token
}
