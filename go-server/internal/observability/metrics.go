package observability

import (
	"net/http"
	"strconv"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

type Metrics struct {
	Registry                  *prometheus.Registry
	Enabled                   bool
	HTTPRequestsTotal         *prometheus.CounterVec
	HTTPRequestDuration       *prometheus.HistogramVec
	HTTPRequestsInFlight      *prometheus.GaugeVec
	HTTPRequestBodyBytes      *prometheus.CounterVec
	HTTPResponseBodyBytes     *prometheus.CounterVec
	JobsCreatedTotal          *prometheus.CounterVec
	JobsQueuedTotal           *prometheus.CounterVec
	JobsStartedTotal          *prometheus.CounterVec
	JobsFinishedTotal         *prometheus.CounterVec
	JobsFailedTotal           *prometheus.CounterVec
	JobDurationSeconds        *prometheus.HistogramVec
	JobAttemptsTotal          *prometheus.CounterVec
	JobCancelRequestedTotal   *prometheus.CounterVec
	WorkerRunningJobs         *prometheus.GaugeVec
	WorkerLimiterInUse        *prometheus.GaugeVec
	WorkerLimiterCapacity     *prometheus.GaugeVec
	PythonRunsTotal           *prometheus.CounterVec
	PythonRunDurationSeconds  *prometheus.HistogramVec
	PythonProcessRunning      *prometheus.GaugeVec
	PythonProcessExitTotal    *prometheus.CounterVec
	PythonArtifactValidation  *prometheus.CounterVec
	PythonArtifactsRegistered *prometheus.CounterVec
	FileUploadTotal           *prometheus.CounterVec
	FileUploadBytesTotal      *prometheus.CounterVec
	FileDownloadTotal         *prometheus.CounterVec
	ArtifactDownloadTotal     *prometheus.CounterVec
	ArtifactRegisterTotal     *prometheus.CounterVec
	FillRunsTotal             *prometheus.CounterVec
	IngestionRunsTotal        *prometheus.CounterVec
	ReviewItemsTotal          *prometheus.CounterVec
	ReviewActionsTotal        *prometheus.CounterVec
	SSEConnectionsCurrent     prometheus.Gauge
	SSEConnectionsTotal       prometheus.Counter
	SSEEventsSentTotal        *prometheus.CounterVec
	SSEClientDisconnectTotal  prometheus.Counter
	AppReadyChecksTotal       *prometheus.CounterVec
}

func NewMetrics(enabled ...bool) *Metrics {
	isEnabled := true
	if len(enabled) > 0 {
		isEnabled = enabled[0]
	}
	registry := prometheus.NewRegistry()
	metrics := &Metrics{
		Enabled:  isEnabled,
		Registry: registry,
		HTTPRequestsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "http_requests_total", Help: "Total HTTP requests."},
			[]string{"method", "path", "status", "code"},
		),
		HTTPRequestDuration: prometheus.NewHistogramVec(
			prometheus.HistogramOpts{Name: "http_request_duration_seconds", Help: "HTTP request latency in seconds."},
			[]string{"method", "path", "status", "code"},
		),
		HTTPRequestsInFlight: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{Name: "http_requests_in_flight", Help: "Current in-flight HTTP requests."},
			[]string{"method", "path"},
		),
		HTTPRequestBodyBytes: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "http_request_body_bytes", Help: "HTTP request body bytes."},
			[]string{"method", "path"},
		),
		HTTPResponseBodyBytes: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "http_response_body_bytes", Help: "HTTP response body bytes."},
			[]string{"method", "path", "status"},
		),
		JobsCreatedTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "jobs_created_total", Help: "Jobs created by type."},
			[]string{"job_type"},
		),
		JobsQueuedTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "jobs_queued_total", Help: "Jobs queued by type."},
			[]string{"job_type"},
		),
		JobsStartedTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "jobs_started_total", Help: "Jobs started by type."},
			[]string{"job_type"},
		),
		JobsFinishedTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "jobs_finished_total", Help: "Jobs finished by type and status."},
			[]string{"job_type", "status"},
		),
		JobsFailedTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "jobs_failed_total", Help: "Jobs failed by type and error class."},
			[]string{"job_type", "error_class"},
		),
		JobDurationSeconds: prometheus.NewHistogramVec(
			prometheus.HistogramOpts{Name: "job_duration_seconds", Help: "Job duration in seconds."},
			[]string{"job_type", "status"},
		),
		JobAttemptsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "job_attempts_total", Help: "Job attempts by type."},
			[]string{"job_type"},
		),
		JobCancelRequestedTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "job_cancel_requested_total", Help: "Job cancel requests by type."},
			[]string{"job_type"},
		),
		WorkerRunningJobs: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{Name: "worker_running_jobs", Help: "Worker running jobs by type."},
			[]string{"job_type"},
		),
		WorkerLimiterInUse: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{Name: "worker_limiter_in_use", Help: "Worker resource limiter in-use slots."},
			[]string{"resource"},
		),
		WorkerLimiterCapacity: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{Name: "worker_limiter_capacity", Help: "Worker resource limiter capacity."},
			[]string{"resource"},
		),
		PythonRunsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "python_runs_total", Help: "Python runs by command and status."},
			[]string{"command", "status"},
		),
		PythonRunDurationSeconds: prometheus.NewHistogramVec(
			prometheus.HistogramOpts{Name: "python_run_duration_seconds", Help: "Python run duration in seconds."},
			[]string{"command", "status"},
		),
		PythonProcessRunning: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{Name: "python_process_running", Help: "Running Python processes."},
			[]string{"command"},
		),
		PythonProcessExitTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "python_process_exit_total", Help: "Python process exits."},
			[]string{"command", "exit_code"},
		),
		PythonArtifactValidation: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "python_artifact_validation_total", Help: "Python artifact validation results."},
			[]string{"status"},
		),
		PythonArtifactsRegistered: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "python_artifacts_registered_total", Help: "Python artifacts registered."},
			[]string{"artifact_type"},
		),
		FileUploadTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "file_upload_total", Help: "File uploads by category and status."},
			[]string{"category", "status"},
		),
		FileUploadBytesTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "file_upload_bytes_total", Help: "File upload bytes by category."},
			[]string{"category"},
		),
		FileDownloadTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "file_download_total", Help: "File downloads by category and status."},
			[]string{"category", "status"},
		),
		ArtifactDownloadTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "artifact_download_total", Help: "Artifact downloads by type and status."},
			[]string{"artifact_type", "status"},
		),
		ArtifactRegisterTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "artifact_register_total", Help: "Artifact registrations by type and status."},
			[]string{"artifact_type", "status"},
		),
		FillRunsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "fill_runs_total", Help: "Fill runs by status."},
			[]string{"status"},
		),
		IngestionRunsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "ingestion_runs_total", Help: "Ingestion runs by status."},
			[]string{"status"},
		),
		ReviewItemsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "review_items_total", Help: "Review items by status and risk."},
			[]string{"status", "risk_level"},
		),
		ReviewActionsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "review_actions_total", Help: "Review actions."},
			[]string{"action"},
		),
		SSEConnectionsCurrent: prometheus.NewGauge(
			prometheus.GaugeOpts{Name: "sse_connections_current", Help: "Current SSE connections."},
		),
		SSEConnectionsTotal: prometheus.NewCounter(
			prometheus.CounterOpts{Name: "sse_connections_total", Help: "Total SSE connections."},
		),
		SSEEventsSentTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "sse_events_sent_total", Help: "SSE events sent by type."},
			[]string{"event_type"},
		),
		SSEClientDisconnectTotal: prometheus.NewCounter(
			prometheus.CounterOpts{Name: "sse_client_disconnect_total", Help: "SSE client disconnects."},
		),
		AppReadyChecksTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "app_ready_checks_total", Help: "Total readiness checks by component and result."},
			[]string{"component", "status"},
		),
	}
	registry.MustRegister(
		metrics.HTTPRequestsTotal, metrics.HTTPRequestDuration, metrics.HTTPRequestsInFlight,
		metrics.HTTPRequestBodyBytes, metrics.HTTPResponseBodyBytes,
		metrics.JobsCreatedTotal, metrics.JobsQueuedTotal, metrics.JobsStartedTotal,
		metrics.JobsFinishedTotal, metrics.JobsFailedTotal, metrics.JobDurationSeconds,
		metrics.JobAttemptsTotal, metrics.JobCancelRequestedTotal, metrics.WorkerRunningJobs,
		metrics.WorkerLimiterInUse, metrics.WorkerLimiterCapacity,
		metrics.PythonRunsTotal, metrics.PythonRunDurationSeconds, metrics.PythonProcessRunning,
		metrics.PythonProcessExitTotal, metrics.PythonArtifactValidation, metrics.PythonArtifactsRegistered,
		metrics.FileUploadTotal, metrics.FileUploadBytesTotal, metrics.FileDownloadTotal,
		metrics.ArtifactDownloadTotal, metrics.ArtifactRegisterTotal,
		metrics.FillRunsTotal, metrics.IngestionRunsTotal, metrics.ReviewItemsTotal,
		metrics.ReviewActionsTotal, metrics.SSEConnectionsCurrent, metrics.SSEConnectionsTotal,
		metrics.SSEEventsSentTotal, metrics.SSEClientDisconnectTotal, metrics.AppReadyChecksTotal,
	)
	return metrics
}

func (m *Metrics) Handler() http.Handler {
	return promhttp.HandlerFor(m.Registry, promhttp.HandlerOpts{})
}

func (m *Metrics) HTTPMiddleware(next http.Handler) http.Handler {
	if m == nil || !m.Enabled {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		path := middleware.RoutePattern(r)
		m.HTTPRequestsInFlight.WithLabelValues(r.Method, path).Inc()
		defer m.HTTPRequestsInFlight.WithLabelValues(r.Method, path).Dec()
		if r.ContentLength > 0 {
			m.HTTPRequestBodyBytes.WithLabelValues(r.Method, path).Add(float64(r.ContentLength))
		}
		recorder := middleware.NewStatusRecorder(w)
		next.ServeHTTP(recorder, r)
		path = middleware.RoutePattern(r)
		status := strconv.Itoa(recorder.Status)
		code := "HTTP_" + status
		m.HTTPRequestsTotal.WithLabelValues(r.Method, path, status, code).Inc()
		m.HTTPRequestDuration.WithLabelValues(r.Method, path, status, code).Observe(time.Since(started).Seconds())
		m.HTTPResponseBodyBytes.WithLabelValues(r.Method, path, status).Add(float64(recorder.BytesWritten))
	})
}

func (m *Metrics) ObserveReadyCheck(component string, ok bool) {
	result := "ok"
	if !ok {
		result = "failed"
	}
	if m == nil || !m.Enabled {
		return
	}
	m.AppReadyChecksTotal.WithLabelValues(component, result).Inc()
}

func (m *Metrics) ObserveJobCreated(jobType string) {
	if m == nil || !m.Enabled {
		return
	}
	m.JobsCreatedTotal.WithLabelValues(safeLabel(jobType, "unknown")).Inc()
}

func (m *Metrics) ObserveJobQueued(jobType string) {
	if m == nil || !m.Enabled {
		return
	}
	m.JobsQueuedTotal.WithLabelValues(safeLabel(jobType, "unknown")).Inc()
}

func (m *Metrics) ObserveJobStarted(jobType string) {
	if m == nil || !m.Enabled {
		return
	}
	m.JobsStartedTotal.WithLabelValues(safeLabel(jobType, "unknown")).Inc()
}

func (m *Metrics) ObserveJobFinished(jobType string, status string, duration time.Duration) {
	if m == nil || !m.Enabled {
		return
	}
	jobType = safeLabel(jobType, "unknown")
	status = safeLabel(status, "unknown")
	m.JobsFinishedTotal.WithLabelValues(jobType, status).Inc()
	if duration < 0 {
		duration = 0
	}
	m.JobDurationSeconds.WithLabelValues(jobType, status).Observe(duration.Seconds())
}

func (m *Metrics) ObserveJobFailed(jobType string, errorClass string) {
	if m == nil || !m.Enabled {
		return
	}
	m.JobsFailedTotal.WithLabelValues(safeLabel(jobType, "unknown"), safeLabel(errorClass, "unknown")).Inc()
}

func (m *Metrics) ObserveJobAttempt(jobType string) {
	if m == nil || !m.Enabled {
		return
	}
	m.JobAttemptsTotal.WithLabelValues(safeLabel(jobType, "unknown")).Inc()
}

func (m *Metrics) ObserveJobCancelRequested(jobType string) {
	if m == nil || !m.Enabled {
		return
	}
	m.JobCancelRequestedTotal.WithLabelValues(safeLabel(jobType, "unknown")).Inc()
}

func (m *Metrics) ObserveWorkerRunning(jobType string, delta float64) {
	if m == nil || !m.Enabled {
		return
	}
	m.WorkerRunningJobs.WithLabelValues(safeLabel(jobType, "unknown")).Add(delta)
}

func (m *Metrics) ObservePythonRun(command string, status string, duration time.Duration) {
	if m == nil || !m.Enabled {
		return
	}
	command = safeLabel(command, "unknown")
	status = safeLabel(status, "unknown")
	m.PythonRunsTotal.WithLabelValues(command, status).Inc()
	if duration < 0 {
		duration = 0
	}
	m.PythonRunDurationSeconds.WithLabelValues(command, status).Observe(duration.Seconds())
}

func (m *Metrics) ObservePythonProcessRunning(command string, delta float64) {
	if m == nil || !m.Enabled {
		return
	}
	m.PythonProcessRunning.WithLabelValues(safeLabel(command, "unknown")).Add(delta)
}

func (m *Metrics) ObservePythonProcessExit(command string, exitCode int) {
	if m == nil || !m.Enabled {
		return
	}
	m.PythonProcessExitTotal.WithLabelValues(safeLabel(command, "unknown"), strconv.Itoa(exitCode)).Inc()
}

func (m *Metrics) ObserveSSEConnect() {
	if m == nil || !m.Enabled {
		return
	}
	m.SSEConnectionsCurrent.Inc()
	m.SSEConnectionsTotal.Inc()
}

func (m *Metrics) ObserveSSEDisconnect() {
	if m == nil || !m.Enabled {
		return
	}
	m.SSEConnectionsCurrent.Dec()
	m.SSEClientDisconnectTotal.Inc()
}

func (m *Metrics) ObserveSSEEvent(eventType string) {
	if m == nil || !m.Enabled {
		return
	}
	m.SSEEventsSentTotal.WithLabelValues(safeLabel(eventType, "unknown")).Inc()
}

func (m *Metrics) ObserveReviewAction(action string) {
	if m == nil || !m.Enabled {
		return
	}
	m.ReviewActionsTotal.WithLabelValues(safeLabel(action, "unknown")).Inc()
}

func safeLabel(value string, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}
