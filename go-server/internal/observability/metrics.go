package observability

import (
	"net/http"
	"strconv"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

type Metrics struct {
	Registry              *prometheus.Registry
	HTTPRequestsTotal     *prometheus.CounterVec
	HTTPRequestDuration   *prometheus.HistogramVec
	HTTPRequestsInFlight  prometheus.Gauge
	AppReadyChecksTotal   *prometheus.CounterVec
}

func NewMetrics() *Metrics {
	registry := prometheus.NewRegistry()
	metrics := &Metrics{
		Registry: registry,
		HTTPRequestsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "http_requests_total", Help: "Total HTTP requests."},
			[]string{"method", "path", "status"},
		),
		HTTPRequestDuration: prometheus.NewHistogramVec(
			prometheus.HistogramOpts{Name: "http_request_duration_seconds", Help: "HTTP request latency in seconds."},
			[]string{"method", "path", "status"},
		),
		HTTPRequestsInFlight: prometheus.NewGauge(
			prometheus.GaugeOpts{Name: "http_requests_in_flight", Help: "Current in-flight HTTP requests."},
		),
		AppReadyChecksTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: "app_ready_checks_total", Help: "Total readiness checks by component and result."},
			[]string{"component", "result"},
		),
	}
	registry.MustRegister(metrics.HTTPRequestsTotal, metrics.HTTPRequestDuration, metrics.HTTPRequestsInFlight, metrics.AppReadyChecksTotal)
	return metrics
}

func (m *Metrics) Handler() http.Handler {
	return promhttp.HandlerFor(m.Registry, promhttp.HandlerOpts{})
}

func (m *Metrics) HTTPMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		m.HTTPRequestsInFlight.Inc()
		defer m.HTTPRequestsInFlight.Dec()
		recorder := middleware.NewStatusRecorder(w)
		next.ServeHTTP(recorder, r)
		path := routePattern(r)
		status := strconv.Itoa(recorder.Status)
		m.HTTPRequestsTotal.WithLabelValues(r.Method, path, status).Inc()
		m.HTTPRequestDuration.WithLabelValues(r.Method, path, status).Observe(time.Since(started).Seconds())
	})
}

func (m *Metrics) ObserveReadyCheck(component string, ok bool) {
	result := "ok"
	if !ok {
		result = "failed"
	}
	m.AppReadyChecksTotal.WithLabelValues(component, result).Inc()
}

func routePattern(r *http.Request) string {
	if ctx := chi.RouteContext(r.Context()); ctx != nil {
		if pattern := ctx.RoutePattern(); pattern != "" {
			return pattern
		}
	}
	// TODO(Block 8): ensure every API path reports a bounded route template.
	return r.URL.Path
}
