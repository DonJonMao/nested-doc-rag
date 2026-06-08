package tests

import (
	"strings"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/stretchr/testify/require"
)

func TestLoadConfigExample(t *testing.T) {
	cfg, err := config.Load("../configs/config.example.yaml")

	require.NoError(t, err)
	require.Equal(t, ":8080", cfg.Server.Addr)
	require.Equal(t, "localhost:6379", cfg.Redis.Addr)
	require.Equal(t, "minio", cfg.Storage.Type)
	require.Equal(t, "python", cfg.Python.Executable)
	require.Equal(t, 2, cfg.Jobs.FillConcurrency)
}

func TestEnvOverride(t *testing.T) {
	t.Setenv("GONGKAN_SERVER_ADDR", ":18080")
	t.Setenv("GONGKAN_DATABASE_DSN", "postgres://override")
	t.Setenv("GONGKAN_STORAGE_TYPE", "local")
	t.Setenv("GONGKAN_STORAGE_LOCAL_DIR", t.TempDir())
	t.Setenv("GONGKAN_PYTHON_EXECUTABLE", "python3")

	cfg, err := config.Load("../configs/config.example.yaml")

	require.NoError(t, err)
	require.Equal(t, ":18080", cfg.Server.Addr)
	require.Equal(t, "postgres://override", cfg.Database.DSN)
	require.Equal(t, "local", cfg.Storage.Type)
	require.Equal(t, "python3", cfg.Python.Executable)
}

func TestValidateMissingField(t *testing.T) {
	cfg := config.Default()
	cfg.Database.DSN = ""

	err := config.Validate(cfg)

	require.Error(t, err)
	require.True(t, strings.Contains(err.Error(), "database.dsn is required"))
}

func TestEnvOverrideInvalidValue(t *testing.T) {
	t.Setenv("GONGKAN_JOBS_FILL_CONCURRENCY", "not-an-int")
	cfg := config.Default()

	err := config.ApplyEnvOverrides(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "GONGKAN_JOBS_FILL_CONCURRENCY")
}
