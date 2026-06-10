package middleware

import (
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/go-chi/chi/v5"
	"go.uber.org/zap"
)

type StatusRecorder struct {
	http.ResponseWriter
	Status       int
	BytesWritten int
}

func NewStatusRecorder(w http.ResponseWriter) *StatusRecorder {
	return &StatusRecorder{ResponseWriter: w, Status: http.StatusOK}
}

func (r *StatusRecorder) WriteHeader(status int) {
	r.Status = status
	r.ResponseWriter.WriteHeader(status)
}

func (r *StatusRecorder) Write(data []byte) (int, error) {
	n, err := r.ResponseWriter.Write(data)
	r.BytesWritten += n
	return n, err
}

func (r *StatusRecorder) Flush() {
	if flusher, ok := r.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func Logger(logger *zap.Logger) func(http.Handler) http.Handler {
	if logger == nil {
		logger = zap.NewNop()
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			started := time.Now()
			recorder := NewStatusRecorder(w)
			next.ServeHTTP(recorder, r)
			fields := []zap.Field{
				zap.String("request_id", recorder.Header().Get(httpx.RequestIDHeader)),
				zap.String("method", r.Method),
				zap.String("path", r.URL.Path),
				zap.String("route", RoutePattern(r)),
				zap.Int("status", recorder.Status),
				zap.Float64("latency_ms", float64(time.Since(started).Microseconds())/1000.0),
				zap.String("remote_addr", r.RemoteAddr),
				zap.String("user_agent", r.UserAgent()),
			}
			if actor, ok := auth.PrincipalFromContext(r.Context()); ok {
				fields = append(fields, zap.String("user_id", actor.UserID.String()))
			}
			logger.Info(
				"http request completed",
				fields...,
			)
		})
	}
}

func RoutePattern(r *http.Request) string {
	if ctx := chi.RouteContext(r.Context()); ctx != nil {
		if pattern := ctx.RoutePattern(); pattern != "" {
			return pattern
		}
	}
	return NormalizePath(r.URL.Path)
}

func NormalizePath(path string) string {
	if path == "" {
		return "/"
	}
	parts := strings.Split(path, "/")
	for i, part := range parts {
		if uuidPathPartPattern.MatchString(part) {
			parts[i] = "{uuid}"
			continue
		}
		if numericPathPartPattern.MatchString(part) {
			parts[i] = "{id}"
		}
	}
	return strings.Join(parts, "/")
}

var (
	uuidPathPartPattern    = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	numericPathPartPattern = regexp.MustCompile(`^[0-9]+$`)
)
