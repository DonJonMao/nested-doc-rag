package middleware

import (
	"net/http"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"go.uber.org/zap"
)

type StatusRecorder struct {
	http.ResponseWriter
	Status int
}

func NewStatusRecorder(w http.ResponseWriter) *StatusRecorder {
	return &StatusRecorder{ResponseWriter: w, Status: http.StatusOK}
}

func (r *StatusRecorder) WriteHeader(status int) {
	r.Status = status
	r.ResponseWriter.WriteHeader(status)
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
			logger.Info(
				"http request completed",
				zap.String("request_id", recorder.Header().Get(httpx.RequestIDHeader)),
				zap.String("method", r.Method),
				zap.String("path", r.URL.Path),
				zap.Int("status", recorder.Status),
				zap.Float64("latency_ms", float64(time.Since(started).Microseconds())/1000.0),
				zap.String("remote_addr", r.RemoteAddr),
				zap.String("user_agent", r.UserAgent()),
			)
		})
	}
}
