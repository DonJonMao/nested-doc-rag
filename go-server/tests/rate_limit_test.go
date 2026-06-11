package tests

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/stretchr/testify/require"
)

func TestRateLimitBelowLimitOK(t *testing.T) {
	handler := rateLimitedHandler(2)

	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.RemoteAddr = "192.0.2.10:1234"
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	require.Equal(t, http.StatusOK, rec.Code)

	rec = httptest.NewRecorder()
	req = httptest.NewRequest(http.MethodGet, "/", nil)
	req.RemoteAddr = "192.0.2.10:1234"
	handler.ServeHTTP(rec, req)
	require.Equal(t, http.StatusOK, rec.Code)
}

func TestRateLimitAboveLimitReturns429(t *testing.T) {
	handler := rateLimitedHandler(1)
	req1 := httptest.NewRequest(http.MethodGet, "/", nil)
	req1.RemoteAddr = "192.0.2.20:1234"
	handler.ServeHTTP(httptest.NewRecorder(), req1)

	rec := httptest.NewRecorder()
	req2 := httptest.NewRequest(http.MethodGet, "/", nil)
	req2.RemoteAddr = "192.0.2.20:1234"
	handler.ServeHTTP(rec, req2)

	require.Equal(t, http.StatusTooManyRequests, rec.Code)
	require.Contains(t, rec.Body.String(), "RATE_LIMITED")
}

func TestRateLimitDifferentIPIndependent(t *testing.T) {
	handler := rateLimitedHandler(1)
	req1 := httptest.NewRequest(http.MethodGet, "/", nil)
	req1.RemoteAddr = "192.0.2.30:1234"
	handler.ServeHTTP(httptest.NewRecorder(), req1)

	rec := httptest.NewRecorder()
	req2 := httptest.NewRequest(http.MethodGet, "/", nil)
	req2.RemoteAddr = "192.0.2.31:1234"
	handler.ServeHTTP(rec, req2)

	require.Equal(t, http.StatusOK, rec.Code)
}

func TestRateLimitDisabledPassThrough(t *testing.T) {
	cfg := config.Default().Security
	cfg.RateLimitEnabled = false
	cfg.RateLimitBurst = 1
	handler := middleware.RateLimit(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	for i := 0; i < 5; i++ {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/", nil)
		req.RemoteAddr = "192.0.2.40:1234"
		req.Header.Set("Authorization", "Bearer should-not-leak")
		handler.ServeHTTP(rec, req)
		require.Equal(t, http.StatusOK, rec.Code)
		require.NotContains(t, rec.Body.String(), "should-not-leak")
	}
}

func rateLimitedHandler(burst int) http.Handler {
	cfg := config.Default().Security
	cfg.RateLimitEnabled = true
	cfg.RateLimitRPS = 1
	cfg.RateLimitBurst = burst
	return middleware.RateLimit(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
}
