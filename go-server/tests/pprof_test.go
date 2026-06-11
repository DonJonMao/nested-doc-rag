package tests

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/observability"
	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestPprofDisabledByDefault(t *testing.T) {
	cfg := config.Default()

	server := observability.NewPprofServer(cfg.Observability, zap.NewNop())

	require.Nil(t, server)
}

func TestPprofEnabledBuildsLocalServer(t *testing.T) {
	cfg := config.Default()
	cfg.Observability.PprofEnabled = true
	cfg.Observability.PprofAddr = "127.0.0.1:0"

	server := observability.NewPprofServer(cfg.Observability, zap.NewNop())

	require.NotNil(t, server)
	require.Equal(t, "127.0.0.1:0", server.Addr)
	rec := httptest.NewRecorder()
	server.Handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/debug/pprof/", nil))
	require.Equal(t, http.StatusOK, rec.Code)
}

func TestPprofNotMountedOnMainRouter(t *testing.T) {
	router := chi.NewRouter()
	httpx.RegisterRoutes(router, httpx.RouteDeps{})

	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/debug/pprof/", nil))

	require.Equal(t, http.StatusNotFound, rec.Code)
}
