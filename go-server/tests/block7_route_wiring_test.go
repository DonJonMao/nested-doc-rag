package tests

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	reviewpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/review"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestBlock7RoutesRequireAuth(t *testing.T) {
	router := block7RouteWiringRouter(t)
	runID := uuid.NewString()
	itemID := uuid.NewString()
	cases := []struct {
		method string
		path   string
	}{
		{http.MethodGet, "/api/v1/fill-runs/" + runID + "/review-items"},
		{http.MethodGet, "/api/v1/fill-runs/" + runID + "/review-items/export"},
		{http.MethodGet, "/api/v1/fill-runs/" + runID + "/result"},
		{http.MethodGet, "/api/v1/review-items/" + itemID},
		{http.MethodPost, "/api/v1/review-items/" + itemID + "/approve"},
		{http.MethodPost, "/api/v1/review-items/" + itemID + "/reject"},
		{http.MethodPost, "/api/v1/review-items/" + itemID + "/edit"},
		{http.MethodPost, "/api/v1/review-items/" + itemID + "/ignore"},
		{http.MethodPost, "/api/v1/review-items/" + itemID + "/reopen"},
	}

	for _, tc := range cases {
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, httptest.NewRequest(tc.method, tc.path, nil))

		require.Equal(t, http.StatusUnauthorized, rec.Code, tc.path)
		require.Contains(t, rec.Body.String(), httpx.CodeUnauthorized, tc.path)
	}
}

func TestBlock7RoutesExistWithAuth(t *testing.T) {
	router := block7RouteWiringRouter(t)
	token := block7RouteToken(t)
	req := httptest.NewRequest(http.MethodGet, "/api/v1/fill-runs/"+uuid.NewString()+"/review-items", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()

	router.ServeHTTP(rec, req)

	require.NotEqual(t, http.StatusNotFound, rec.Code)
}

func block7RouteWiringRouter(t *testing.T) http.Handler {
	t.Helper()
	manager, err := auth.NewTokenManager("block7-route-secret", time.Minute)
	require.NoError(t, err)
	router := chi.NewRouter()
	router.Use(middleware.RequestID)
	router.Route("/api/v1", func(api chi.Router) {
		api.Group(func(protected chi.Router) {
			protected.Use(middleware.Auth(manager))
			reviewpkg.NewHandler(&fakeReviewUseCase{}, &fakeReviewResultRuns{}).RegisterRoutes(protected)
		})
	})
	return router
}

func block7RouteToken(t *testing.T) string {
	t.Helper()
	manager, err := auth.NewTokenManager("block7-route-secret", time.Minute)
	require.NoError(t, err)
	token, _, err := manager.GenerateAccessToken(auth.User{ID: uuid.New(), Username: "route-user"}, []string{auth.RoleOperator})
	require.NoError(t, err)
	return token
}
