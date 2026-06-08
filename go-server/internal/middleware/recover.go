package middleware

import (
	"net/http"
	"runtime/debug"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"go.uber.org/zap"
)

func Recover(logger *zap.Logger) func(http.Handler) http.Handler {
	if logger == nil {
		logger = zap.NewNop()
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if recovered := recover(); recovered != nil {
					logger.Error(
						"panic recovered",
						zap.Any("panic", recovered),
						zap.ByteString("stack", debug.Stack()),
						zap.String("request_id", w.Header().Get(httpx.RequestIDHeader)),
						zap.String("method", r.Method),
						zap.String("path", r.URL.Path),
					)
					httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInternal, "internal server error", http.StatusInternalServerError, nil, nil))
				}
			}()
			next.ServeHTTP(w, r)
		})
	}
}
