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
	require.Equal(t, 4, cfg.Jobs.WorkerConcurrency)
	require.Equal(t, 3, cfg.Jobs.MaxAttempts)
	require.Equal(t, "gongkan", cfg.Jobs.RedisNamespace)
	require.Equal(t, "2h0m0s", cfg.Jobs.DefaultTimeout.String())
	require.Equal(t, "30s", cfg.Jobs.RetryBackoff.String())
	require.Equal(t, "10s", cfg.Jobs.HeartbeatInterval.String())
	require.Equal(t, 256, cfg.Jobs.EventBufferSize)
	require.False(t, cfg.Jobs.EnableNoopJob)
	require.Equal(t, int64(200*1024*1024), cfg.Files.MaxUploadSize.Bytes)
	require.Equal(t, "./runtime/tmp/uploads", cfg.Files.TempDir)
	require.Contains(t, cfg.Files.AllowedExtensions, ".xlsx")
	require.Equal(t, "proxy", cfg.Artifacts.DownloadMode)
	require.False(t, cfg.Artifacts.AllowPresignDownload)
	require.Equal(t, "15m0s", cfg.Artifacts.DefaultPresignTTL.String())
}

func TestEnvOverride(t *testing.T) {
	t.Setenv("GONGKAN_SERVER_ADDR", ":18080")
	t.Setenv("GONGKAN_DATABASE_DSN", "postgres://override")
	t.Setenv("GONGKAN_STORAGE_TYPE", "local")
	t.Setenv("GONGKAN_STORAGE_LOCAL_DIR", t.TempDir())
	t.Setenv("GONGKAN_PYTHON_EXECUTABLE", "python3")
	t.Setenv("GONGKAN_FILES_MAX_UPLOAD_SIZE", "20MB")
	t.Setenv("GONGKAN_FILES_TEMP_DIR", "/tmp/gongkan-uploads")
	t.Setenv("GONGKAN_ARTIFACTS_DOWNLOAD_MODE", "presign")
	t.Setenv("GONGKAN_ARTIFACTS_ALLOW_PRESIGN_DOWNLOAD", "true")
	t.Setenv("GONGKAN_JOBS_WORKER_CONCURRENCY", "7")
	t.Setenv("GONGKAN_JOBS_MAX_ATTEMPTS", "5")
	t.Setenv("GONGKAN_JOBS_DEFAULT_TIMEOUT", "30m")
	t.Setenv("GONGKAN_JOBS_RETRY_BACKOFF", "5s")
	t.Setenv("GONGKAN_JOBS_REDIS_NAMESPACE", "override")
	t.Setenv("GONGKAN_JOBS_ENABLE_NOOP_JOB", "true")

	cfg, err := config.Load("../configs/config.example.yaml")

	require.NoError(t, err)
	require.Equal(t, ":18080", cfg.Server.Addr)
	require.Equal(t, "postgres://override", cfg.Database.DSN)
	require.Equal(t, "local", cfg.Storage.Type)
	require.Equal(t, "python3", cfg.Python.Executable)
	require.Equal(t, int64(20*1024*1024), cfg.Files.MaxUploadSize.Bytes)
	require.Equal(t, "/tmp/gongkan-uploads", cfg.Files.TempDir)
	require.Equal(t, "presign", cfg.Artifacts.DownloadMode)
	require.True(t, cfg.Artifacts.AllowPresignDownload)
	require.Equal(t, 7, cfg.Jobs.WorkerConcurrency)
	require.Equal(t, 5, cfg.Jobs.MaxAttempts)
	require.Equal(t, "30m0s", cfg.Jobs.DefaultTimeout.String())
	require.Equal(t, "5s", cfg.Jobs.RetryBackoff.String())
	require.Equal(t, "override", cfg.Jobs.RedisNamespace)
	require.True(t, cfg.Jobs.EnableNoopJob)
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

func TestValidateInvalidDownloadMode(t *testing.T) {
	cfg := config.Default()
	cfg.Artifacts.DownloadMode = "bad"

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "artifacts.download_mode")
}

func TestValidateInvalidMaxUploadSize(t *testing.T) {
	cfg := config.Default()
	cfg.Files.MaxUploadSize = config.NewByteSize(0)

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "files.max_upload_size")
}

func TestValidateMissingAllowedExtensions(t *testing.T) {
	cfg := config.Default()
	cfg.Files.AllowedExtensions = nil

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "files.allowed_extensions")
}

func TestValidateInvalidJobsConfig(t *testing.T) {
	cfg := config.Default()
	cfg.Jobs.WorkerConcurrency = 0
	cfg.Jobs.MaxAttempts = 0
	cfg.Jobs.DefaultTimeout = config.NewDuration(0)
	cfg.Jobs.RetryBackoff = config.NewDuration(0)
	cfg.Jobs.EventBufferSize = 0

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "jobs.worker_concurrency")
	require.Contains(t, err.Error(), "jobs.max_attempts")
	require.Contains(t, err.Error(), "jobs.default_timeout")
	require.Contains(t, err.Error(), "jobs.retry_backoff")
	require.Contains(t, err.Error(), "jobs.event_buffer_size")
}
