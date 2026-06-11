package tests

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/observability"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestTracingDisabledNoop(t *testing.T) {
	provider := observability.NewTracerProvider(config.Default().Observability, zap.NewNop())
	called := false
	handler := provider.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		w.WriteHeader(http.StatusNoContent)
	}))

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))

	require.True(t, called)
	require.Equal(t, http.StatusNoContent, rec.Code)
	require.NoError(t, provider.Shutdown(context.Background()))
}

func TestTracingHTTPMiddlewareDoesNotBreakRequest(t *testing.T) {
	cfg := config.Default().Observability
	cfg.TracingEnabled = true
	cfg.TracingExporter = "none"
	provider := observability.NewTracerProvider(cfg, zap.NewNop())
	called := false
	handler := provider.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		w.WriteHeader(http.StatusAccepted)
	}))

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/v1/ping", nil))

	require.True(t, called)
	require.Equal(t, http.StatusAccepted, rec.Code)
	require.NoError(t, provider.Shutdown(context.Background()))
}

func TestTracingInvalidExporterRejectedByConfig(t *testing.T) {
	cfg := config.Default()
	cfg.Observability.TracingExporter = "bad"

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "observability.tracing_exporter")
}

func TestTracingStdoutExporterConfigAccepted(t *testing.T) {
	cfg := config.Default()
	cfg.Observability.TracingEnabled = true
	cfg.Observability.TracingExporter = "stdout"

	err := config.Validate(cfg)

	require.NoError(t, err)
}

func TestTracingHelpersShutdownSafe(t *testing.T) {
	ctx, jobSpan := observability.StartJobSpan(context.Background(), "fill_form")
	require.NotNil(t, ctx)
	jobSpan.End()

	ctx, pythonSpan := observability.StartPythonSpan(ctx, "step15_agent")
	require.NotNil(t, ctx)
	pythonSpan.End()
}
