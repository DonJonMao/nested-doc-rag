package middleware

import (
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
)

func BodyLimit(cfg config.SecurityConfig) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		if !cfg.BodyLimitEnabled || cfg.MaxBodySize.Bytes <= 0 {
			return next
		}
		limit := cfg.MaxBodySize.Bytes
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Body != nil {
				r.Body = http.MaxBytesReader(w, r.Body, limit)
			}
			if r.ContentLength > limit {
				httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInvalidArgument, "request body too large", http.StatusRequestEntityTooLarge, map[string]any{"max_body_size": limit}, nil))
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}
