package observability

import (
	"context"
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"go.uber.org/zap"
)

type TracerProvider interface {
	Shutdown(ctx context.Context) error
	Middleware(next http.Handler) http.Handler
}

type Span interface {
	End()
}

type noopTracerProvider struct {
	enabled bool
	logger  *zap.Logger
}

type noopSpan struct{}

func NewTracerProvider(cfg config.ObservabilityConfig, logger *zap.Logger) TracerProvider {
	provider := &noopTracerProvider{
		enabled: cfg.TracingEnabled,
		logger:  logger,
	}
	if cfg.TracingEnabled && logger != nil {
		logger.Info(
			"tracing exporter is not configured; using no-op tracer",
			zap.String("service", cfg.TracingServiceName),
			zap.String("exporter", cfg.TracingExporter),
		)
	}
	return provider
}

func (p *noopTracerProvider) Shutdown(ctx context.Context) error {
	_ = ctx
	return nil
}

func (p *noopTracerProvider) Middleware(next http.Handler) http.Handler {
	if p == nil || !p.enabled {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if p.logger != nil {
			p.logger.Debug(
				"http trace span",
				zap.String("method", r.Method),
				zap.String("route", middleware.RoutePattern(r)),
				zap.String("request_id", r.Header.Get(httpx.RequestIDHeader)),
			)
		}
		next.ServeHTTP(w, r)
	})
}

func StartJobSpan(ctx context.Context, jobType string) (context.Context, Span) {
	_ = jobType
	return ctx, noopSpan{}
}

func StartPythonSpan(ctx context.Context, command string) (context.Context, Span) {
	_ = command
	return ctx, noopSpan{}
}

func (noopSpan) End() {}
