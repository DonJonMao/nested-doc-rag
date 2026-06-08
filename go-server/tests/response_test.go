package tests

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/stretchr/testify/require"
)

func TestWriteOKFormat(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/v1/ping", nil)
	req.Header.Set(httpx.RequestIDHeader, "req_test")
	rec := httptest.NewRecorder()

	httpx.WriteOK(rec, req, map[string]string{"service": "gongkan-platform"})

	require.Equal(t, http.StatusOK, rec.Code)
	var body map[string]any
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Equal(t, "OK", body["code"])
	require.Equal(t, "success", body["message"])
	require.Equal(t, "req_test", body["request_id"])
	require.Equal(t, "gongkan-platform", body["data"].(map[string]any)["service"])
}

func TestWriteErrorFormat(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/bad", nil)
	req.Header.Set(httpx.RequestIDHeader, "req_error")
	rec := httptest.NewRecorder()
	err := httpx.NewAppError(httpx.CodeInvalidArgument, "invalid rows format", http.StatusBadRequest, map[string]string{"field": "rows"}, nil)

	httpx.WriteError(rec, req, err)

	require.Equal(t, http.StatusBadRequest, rec.Code)
	var body map[string]any
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Equal(t, "INVALID_ARGUMENT", body["code"])
	require.Equal(t, "invalid rows format", body["message"])
	require.Equal(t, "req_error", body["request_id"])
	require.Equal(t, "rows", body["details"].(map[string]any)["field"])
}
