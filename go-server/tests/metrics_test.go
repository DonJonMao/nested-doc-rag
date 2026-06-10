package tests

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/observability"
	"github.com/stretchr/testify/require"
)

func TestMetricsRegisteredWithoutDuplicatePanic(t *testing.T) {
	require.NotPanics(t, func() {
		_ = observability.NewMetrics(true)
		_ = observability.NewMetrics(true)
	})
}

func TestHTTPMetricsUpdate(t *testing.T) {
	metrics := observability.NewMetrics(true)
	handler := metrics.HTTPMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("ok"))
	}))

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/v1/ping", strings.NewReader("body")))

	names := metricNames(t, metrics)
	require.Contains(t, names, "http_requests_total")
	require.Contains(t, names, "http_request_duration_seconds")
	require.Contains(t, names, "http_request_body_bytes")
	require.Contains(t, names, "http_response_body_bytes")
}

func TestJobAndSSEMetricsUpdate(t *testing.T) {
	metrics := observability.NewMetrics(true)

	metrics.ObserveJobCreated("fill_form")
	metrics.ObserveJobQueued("fill_form")
	metrics.ObserveJobStarted("fill_form")
	metrics.ObserveJobFinished("fill_form", "succeeded", 1200_000_000)
	metrics.ObserveJobFailed("fill_form", "internal")
	metrics.ObserveJobAttempt("fill_form")
	metrics.ObserveJobCancelRequested("fill_form")
	metrics.ObserveWorkerRunning("fill_form", 1)
	metrics.WorkerLimiterInUse.WithLabelValues("python").Set(1)
	metrics.WorkerLimiterCapacity.WithLabelValues("python").Set(3)
	metrics.ObservePythonRun("step15_agent", "succeeded", 1000_000_000)
	metrics.ObservePythonProcessRunning("step15_agent", 1)
	metrics.ObservePythonProcessRunning("step15_agent", -1)
	metrics.ObservePythonProcessExit("step15_agent", 0)
	metrics.PythonArtifactValidation.WithLabelValues("ok").Inc()
	metrics.PythonArtifactsRegistered.WithLabelValues("filled_form").Inc()
	metrics.FileUploadTotal.WithLabelValues("form_template", "succeeded").Inc()
	metrics.FileUploadBytesTotal.WithLabelValues("form_template").Add(10)
	metrics.FileDownloadTotal.WithLabelValues("form_template", "succeeded").Inc()
	metrics.ArtifactDownloadTotal.WithLabelValues("filled_form", "succeeded").Inc()
	metrics.ArtifactRegisterTotal.WithLabelValues("filled_form", "succeeded").Inc()
	metrics.FillRunsTotal.WithLabelValues("succeeded").Inc()
	metrics.IngestionRunsTotal.WithLabelValues("succeeded").Inc()
	metrics.ReviewItemsTotal.WithLabelValues("pending", "high").Inc()
	metrics.ObserveSSEConnect()
	metrics.ObserveSSEEvent("fill_finished")
	metrics.ObserveSSEDisconnect()
	metrics.ObserveReviewAction("approve")

	names := metricNames(t, metrics)
	require.Contains(t, names, "jobs_created_total")
	require.Contains(t, names, "jobs_finished_total")
	require.Contains(t, names, "worker_limiter_capacity")
	require.Contains(t, names, "python_runs_total")
	require.Contains(t, names, "python_artifacts_registered_total")
	require.Contains(t, names, "file_upload_total")
	require.Contains(t, names, "artifact_register_total")
	require.Contains(t, names, "fill_runs_total")
	require.Contains(t, names, "ingestion_runs_total")
	require.Contains(t, names, "review_items_total")
	require.Contains(t, names, "sse_connections_total")
	require.Contains(t, names, "sse_events_sent_total")
	require.Contains(t, names, "review_actions_total")
}

func TestMetricsDisabledNoopHTTPMiddleware(t *testing.T) {
	metrics := observability.NewMetrics(false)
	called := false
	handler := metrics.HTTPMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		w.WriteHeader(http.StatusNoContent)
	}))

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))

	require.True(t, called)
	require.Equal(t, http.StatusNoContent, rec.Code)
}

func TestNormalizePathRemovesHighCardinalityIDs(t *testing.T) {
	normalized := middleware.NormalizePath("/api/v1/review-items/8e83d2bb-0871-43e0-8c32-a2d3a6fbbcd4/events/123")

	require.Equal(t, "/api/v1/review-items/{uuid}/events/{id}", normalized)
}

func metricNames(t *testing.T, metrics *observability.Metrics) map[string]struct{} {
	t.Helper()
	gathered, err := metrics.Registry.Gather()
	require.NoError(t, err)
	names := make(map[string]struct{}, len(gathered))
	for _, family := range gathered {
		names[family.GetName()] = struct{}{}
	}
	return names
}
