package tests

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/stretchr/testify/require"
)

func TestSecurityHeadersPresent(t *testing.T) {
	cfg := config.Default().Security
	cfg.HSTSEnabled = false
	handler := middleware.SecurityHeaders(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))

	require.Equal(t, "nosniff", rec.Header().Get("X-Content-Type-Options"))
	require.Equal(t, "DENY", rec.Header().Get("X-Frame-Options"))
	require.Equal(t, "0", rec.Header().Get("X-XSS-Protection"))
	require.Equal(t, "no-referrer", rec.Header().Get("Referrer-Policy"))
	require.Equal(t, "default-src 'none'; frame-ancestors 'none'", rec.Header().Get("Content-Security-Policy"))
	require.Equal(t, "camera=(), microphone=(), geolocation=()", rec.Header().Get("Permissions-Policy"))
	require.Empty(t, rec.Header().Get("Strict-Transport-Security"))
}

func TestSecurityHeadersHSTSEnabled(t *testing.T) {
	cfg := config.Default().Security
	cfg.HSTSEnabled = true
	cfg.HSTSMaxAge = config.NewDuration(24 * time.Hour)
	handler := middleware.SecurityHeaders(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/", nil))

	require.Equal(t, "max-age=86400; includeSubDomains", rec.Header().Get("Strict-Transport-Security"))
}
