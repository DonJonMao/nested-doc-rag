package tests

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestBlock5RoutesRequireAuthAndAreWired(t *testing.T) {
	router := block5RouteWiringRouter(t)
	cases := []struct {
		method string
		path   string
	}{
		{http.MethodPost, "/api/v1/forms"},
		{http.MethodPost, "/api/v1/fill-runs"},
		{http.MethodGet, "/api/v1/fill-runs/" + uuid.NewString() + "/download/filled-form"},
		{http.MethodGet, "/api/v1/fill-runs/" + uuid.NewString() + "/downloads/filled-form"},
		{http.MethodGet, "/api/v1/fill-runs/" + uuid.NewString() + "/downloads/review-items?format=csv"},
	}

	for _, tc := range cases {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(tc.method, tc.path, nil)

		router.ServeHTTP(rec, req)

		require.Equal(t, http.StatusUnauthorized, rec.Code, tc.path)
		require.Contains(t, rec.Body.String(), httpx.CodeUnauthorized, tc.path)
	}
}

func block5RouteWiringRouter(t *testing.T) http.Handler {
	t.Helper()
	manager, err := auth.NewTokenManager("block5-route-secret", time.Minute)
	require.NoError(t, err)
	router := chi.NewRouter()
	router.Use(middleware.RequestID)
	router.Route("/api/v1", func(api chi.Router) {
		api.Group(func(protected chi.Router) {
			protected.Use(middleware.Auth(manager))
			formpkg.NewHandler(&fakeFormUseCase{}, &fakeFillRunUseCase{}).RegisterRoutes(protected)
		})
	})
	return router
}
