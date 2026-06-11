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
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
	"go.uber.org/zap/zaptest/observer"
)

func TestRedactSensitiveMap(t *testing.T) {
	out := logging.RedactSensitiveMap(map[string]any{
		"password":         "secret-password",
		"token":            "raw-token",
		"access_token":     "access-token",
		"refresh_token":    "refresh-token",
		"authorization":    "Bearer token",
		"api_key":          "api-key",
		"secret":           "secret",
		"jwt_secret":       "jwt-secret",
		"minio_secret_key": "minio-secret",
		"dsn":              "postgres://user:pass@localhost/db",
		"database_dsn":     "postgres://user:pass@localhost:5432/db?sslmode=disable",
		"deepseek_api_key": "deepseek-secret",
		"nested":           map[string]any{"openai_api_key": "sk-secret"},
		"non_sensitive":    "keep",
		"connection_text":  "postgres://user:pass@localhost/db password=secret token=abc",
	})

	require.Equal(t, logging.Redacted, out["password"])
	require.Equal(t, logging.Redacted, out["token"])
	require.Equal(t, logging.Redacted, out["access_token"])
	require.Equal(t, logging.Redacted, out["refresh_token"])
	require.Equal(t, logging.Redacted, out["authorization"])
	require.Equal(t, logging.Redacted, out["api_key"])
	require.Equal(t, logging.Redacted, out["secret"])
	require.Equal(t, logging.Redacted, out["jwt_secret"])
	require.Equal(t, logging.Redacted, out["minio_secret_key"])
	require.Equal(t, logging.Redacted, out["dsn"])
	require.Equal(t, logging.Redacted, out["database_dsn"])
	require.Equal(t, logging.Redacted, out["deepseek_api_key"])
	require.Equal(t, logging.Redacted, out["nested"].(map[string]any)["openai_api_key"])
	require.Equal(t, "keep", out["non_sensitive"])
	require.NotContains(t, out["connection_text"], "secret")
	require.NotContains(t, out["connection_text"], "abc")
}

func TestRedactStringDSNPassword(t *testing.T) {
	redacted := logging.RedactString("postgres://gongkan:super-secret@localhost:5432/gongkan?sslmode=disable Bearer abc.def.ghi api_key=xxx")

	require.Contains(t, redacted, logging.Redacted)
	require.NotContains(t, redacted, "super-secret")
	require.NotContains(t, redacted, "abc.def.ghi")
	require.NotContains(t, redacted, "xxx")
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

func TestPythonRedactedArgsDoNotIncludeSecrets(t *testing.T) {
	builder := pythonpkg.CommandBuilder{
		PythonExecutable:  "python",
		ProjectDir:        "/repo",
		DefaultConfigPath: "config/local.yaml",
	}

	spec := builder.BuildStep15AgentCommand(pythonpkg.Step15RunRequest{
		TargetNamespace: "xixian",
		GlobalNamespace: "global",
		RoomContext:     "room",
		Rows:            "4-4",
		RetrievalMode:   "layered",
		PromptVersion:   "v1",
		OutDir:          "/tmp/out",
		Env: map[string]string{
			"OPENAI_API_KEY": "sk-should-not-appear",
			"PASSWORD":       "password-should-not-appear",
		},
	})

	joinedArgs := strings.Join(spec.RedactedArgs, " ")
	joinedEnv := strings.Join(spec.Env, " ")
	require.NotContains(t, joinedArgs, "sk-should-not-appear")
	require.NotContains(t, joinedArgs, "password-should-not-appear")
	require.Contains(t, joinedEnv, "sk-should-not-appear")
}

func mapValues(values map[string]any) []string {
	out := make([]string, 0, len(values))
	for _, value := range values {
		out = append(out, fmt.Sprint(value))
	}
	return out
}
