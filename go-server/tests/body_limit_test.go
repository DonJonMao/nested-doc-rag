package tests

import (
	"bytes"
	"io"
	"mime/multipart"
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

func TestBodyLimitDisabledPassThrough(t *testing.T) {
	cfg := config.Default().Security
	cfg.BodyLimitEnabled = false
	cfg.MaxBodySize = config.NewByteSize(1)
	called := false
	handler := middleware.BodyLimit(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		_, _ = io.Copy(w, r.Body)
	}))

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/", strings.NewReader("large"))
	handler.ServeHTTP(rec, req)

	require.True(t, called)
	require.Equal(t, http.StatusOK, rec.Code)
	require.Equal(t, "large", rec.Body.String())
}

func TestBodyLimitGetWithoutBodyNotRejected(t *testing.T) {
	handler := bodyLimitHandler(1)

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/download", nil)
	handler.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
}

func TestBodyLimitMultipartUnderMaxSizeAccepted(t *testing.T) {
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("file", "small.txt")
	require.NoError(t, err)
	_, err = part.Write([]byte("small"))
	require.NoError(t, err)
	require.NoError(t, writer.WriteField("workspace_id", "workspace"))
	require.NoError(t, writer.Close())

	handler := bodyLimitHandler(int64(body.Len() + 128))
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/files", &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	handler.ServeHTTP(rec, req)

	require.Equal(t, http.StatusOK, rec.Code)
	require.NotEmpty(t, rec.Body.String())
}

func bodyLimitHandler(limit int64) http.Handler {
	cfg := config.Default().Security
	cfg.BodyLimitEnabled = true
	cfg.MaxBodySize = config.NewByteSize(limit)
	return middleware.BodyLimit(cfg)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(w, r.Body)
	}))
}
