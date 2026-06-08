package middleware

import (
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
)

func RequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestID := r.Header.Get(httpx.RequestIDHeader)
		if requestID == "" {
			requestID = uuid.NewString()
		}
		w.Header().Set(httpx.RequestIDHeader, requestID)
		r.Header.Set(httpx.RequestIDHeader, requestID)
		next.ServeHTTP(w, r)
	})
}
