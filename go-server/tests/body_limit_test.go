package tests

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/stretchr/testify/require"
)

func TestBodyLimitTooLargeReturns413(t *testing.T) {
	handler := bodyLimitHandler(4)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/", strings.NewReader("12345"))
	handler.ServeHTTP(rec, req)

	require.Equal(t, http.StatusRequestEntityTooLarge, rec.Code)
	require.Contains(t, rec.Body.String(), "INVALID_ARGUMENT")
}

func TestBodyLimitSmallBodyOK(t *testing.T) {
	handler := bodyLimitHandler(8)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/", strings.NewReader("1234"))
	handler.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.Equal(t, "1234", rec.Body.String())
}

func bodyLimitHandler(limit int64) http.Handler {
	cfg := config.Default().Security
	cfg.BodyLimitEnabled = true
	cfg.MaxBodySize = config.NewByteSize(limit)
	return middleware.BodyLimit(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(w, r.Body)
	}))
}
