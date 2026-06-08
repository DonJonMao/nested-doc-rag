package tests

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/require"
)

func TestHealthzReturnsOK(t *testing.T) {
	router := testRouter(nil)
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)

	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	var body map[string]any
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Equal(t, "OK", body["code"])
	require.Equal(t, "ok", body["data"].(map[string]any)["status"])
	require.NotEmpty(t, body["request_id"])
}

func TestReadyzReturnsOK(t *testing.T) {
	router := testRouter([]httpx.ReadyCheck{
		{Name: "database", Check: func(ctx context.Context) error { return nil }},
		{Name: "redis", Check: func(ctx context.Context) error { return nil }},
		{Name: "storage", Check: func(ctx context.Context) error { return nil }},
	})
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)

	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	var body map[string]any
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	checks := body["data"].(map[string]any)["checks"].(map[string]any)
	require.Equal(t, "ok", checks["database"])
	require.Equal(t, "ok", checks["redis"])
	require.Equal(t, "ok", checks["storage"])
}

func TestReadyzDependencyFailureReturns503(t *testing.T) {
	router := testRouter([]httpx.ReadyCheck{
		{Name: "database", Check: func(ctx context.Context) error { return nil }},
		{Name: "redis", Check: func(ctx context.Context) error { return errors.New("redis down") }},
	})
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/readyz", nil)

	router.ServeHTTP(rec, req)

	require.Equal(t, http.StatusServiceUnavailable, rec.Code)
	var body map[string]any
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Equal(t, "INTERNAL", body["code"])
	details := body["details"].(map[string]any)
	checks := details["checks"].(map[string]any)
	require.Equal(t, "failed", checks["redis"])
}

func testRouter(checks []httpx.ReadyCheck) http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	httpx.RegisterRoutes(r, httpx.RouteDeps{ReadyChecks: checks})
	return r
}
