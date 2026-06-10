package tests

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/logging"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
	"go.uber.org/zap/zaptest/observer"
)

func TestRedactSensitiveMap(t *testing.T) {
	out := logging.RedactSensitiveMap(map[string]any{
		"password":        "secret-password",
		"authorization":   "Bearer token",
		"database_dsn":    "postgres://user:pass@localhost:5432/db?sslmode=disable",
		"nested":          map[string]any{"openai_api_key": "sk-secret"},
		"non_sensitive":   "keep",
		"connection_text": "postgres://user:pass@localhost/db password=secret token=abc",
	})

	require.Equal(t, logging.Redacted, out["password"])
	require.Equal(t, logging.Redacted, out["authorization"])
	require.Equal(t, logging.Redacted, out["database_dsn"])
	require.Equal(t, logging.Redacted, out["nested"].(map[string]any)["openai_api_key"])
	require.Equal(t, "keep", out["non_sensitive"])
	require.NotContains(t, out["connection_text"], "secret")
	require.NotContains(t, out["connection_text"], "abc")
}

func TestRedactStringDSNPassword(t *testing.T) {
	redacted := logging.RedactString("postgres://gongkan:super-secret@localhost:5432/gongkan?sslmode=disable")

	require.Contains(t, redacted, logging.Redacted)
	require.NotContains(t, redacted, "super-secret")
}

func TestAuthorizationHeaderNotLogged(t *testing.T) {
	core, logs := observer.New(zap.InfoLevel)
	logger := zap.New(core)
	handler := middleware.RequestID(middleware.Logger(logger)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})))
	req := httptest.NewRequest(http.MethodGet, "/api/v1/ping", nil)
	req.Header.Set("Authorization", "Bearer should-not-log")

	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	require.Equal(t, http.StatusNoContent, rec.Code)
	encoded := logs.All()[0].ContextMap()
	require.NotContains(t, strings.Join(mapValues(encoded), " "), "should-not-log")
}

func TestConfigRedactedSummary(t *testing.T) {
	cfg := config.Default()
	cfg.Database.DSN = "postgres://user:secret@localhost:5432/db"
	cfg.Storage.MinIO.SecretKey = "minio-secret"

	summary := config.RedactedSummary(*cfg)

	require.Equal(t, "[REDACTED]", summary["database"].(map[string]any)["dsn"])
	require.Equal(t, "[REDACTED]", summary["storage"].(map[string]any)["minio"].(map[string]any)["secret_key"])
}

func mapValues(values map[string]any) []string {
	out := make([]string, 0, len(values))
	for _, value := range values {
		out = append(out, fmt.Sprint(value))
	}
	return out
}
