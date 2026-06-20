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
	require.Equal(t, "../", cfg.Python.ProjectDir)
	require.True(t, cfg.Python.ArtifactValidationEnabled)
	require.Equal(t, "10s", cfg.Python.KillGracePeriod.String())
	require.Equal(t, int64(1048576), cfg.Python.StdoutLogMaxBytes)
	require.Equal(t, int64(1048576), cfg.Python.StderrLogMaxBytes)
	require.Equal(t, "layered", cfg.Python.Step15DefaultRetrievalMode)
	require.Equal(t, "step15_compat", cfg.Python.Step15DefaultPromptVersion)
	require.Equal(t, "4-144", cfg.Python.Step15DefaultRows)
	require.True(t, cfg.Python.IngestCommandEnabled)
	require.Equal(t, 1, cfg.Jobs.FillConcurrency)
	require.Equal(t, 1, cfg.Jobs.IngestionConcurrency)
	require.Equal(t, 1, cfg.Jobs.MaxPythonProcesses)
	require.Equal(t, 4, cfg.Jobs.WorkerConcurrency)
	require.Equal(t, 3, cfg.Jobs.MaxAttempts)
	require.Equal(t, "gongkan", cfg.Jobs.RedisNamespace)
	require.Equal(t, "2h0m0s", cfg.Jobs.DefaultTimeout.String())
	require.Equal(t, "30s", cfg.Jobs.RetryBackoff.String())
	require.Equal(t, "10s", cfg.Jobs.HeartbeatInterval.String())
	require.Equal(t, 256, cfg.Jobs.EventBufferSize)
	require.False(t, cfg.Jobs.EnableNoopJob)
	require.True(t, cfg.Jobs.EventBusEnabled)
	require.Equal(t, "gongkan:run_events", cfg.Jobs.EventChannel)
	require.Equal(t, int64(200*1024*1024), cfg.Files.MaxUploadSize.Bytes)
	require.Equal(t, "./runtime/tmp/uploads", cfg.Files.TempDir)
	require.Contains(t, cfg.Files.AllowedExtensions, ".xlsx")
	require.Contains(t, cfg.Files.AllowedExtensions, ".xlsm")
	require.Contains(t, cfg.Files.AllowedExtensions, ".docx")
	require.Contains(t, cfg.Files.AllowedExtensions, ".txt")
	require.Contains(t, cfg.Files.AllowedExtensions, ".md")
	require.Contains(t, cfg.Files.AllowedExtensions, ".csv")
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
	t.Setenv("GONGKAN_PYTHON_PROJECT_DIR", "/repo")
	t.Setenv("GONGKAN_PYTHON_CONFIG_PATH", "config/test.yaml")
	t.Setenv("GONGKAN_PYTHON_DEFAULT_TIMEOUT", "45m")
	t.Setenv("GONGKAN_PYTHON_ARTIFACT_VALIDATION_ENABLED", "false")
	t.Setenv("GONGKAN_PYTHON_KILL_GRACE_PERIOD", "3s")
	t.Setenv("GONGKAN_PYTHON_STDOUT_LOG_MAX_BYTES", "2048")
	t.Setenv("GONGKAN_PYTHON_STDERR_LOG_MAX_BYTES", "4096")
	t.Setenv("GONGKAN_PYTHON_STEP15_DEFAULT_RETRIEVAL_MODE", "flat")
	t.Setenv("GONGKAN_PYTHON_STEP15_DEFAULT_PROMPT_VERSION", "prompt_v2")
	t.Setenv("GONGKAN_PYTHON_STEP15_DEFAULT_ROWS", "1-2")
	t.Setenv("GONGKAN_PYTHON_INGEST_COMMAND_ENABLED", "true")
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
	t.Setenv("GONGKAN_JOBS_EVENT_BUS_ENABLED", "false")
	t.Setenv("GONGKAN_JOBS_EVENT_CHANNEL", "override:events")

	cfg, err := config.Load("../configs/config.example.yaml")

	require.NoError(t, err)
	require.Equal(t, ":18080", cfg.Server.Addr)
	require.Equal(t, "postgres://override", cfg.Database.DSN)
	require.Equal(t, "local", cfg.Storage.Type)
	require.Equal(t, "python3", cfg.Python.Executable)
	require.Equal(t, "/repo", cfg.Python.ProjectDir)
	require.Equal(t, "config/test.yaml", cfg.Python.ConfigPath)
	require.Equal(t, "45m0s", cfg.Python.DefaultTimeout.String())
	require.False(t, cfg.Python.ArtifactValidationEnabled)
	require.Equal(t, "3s", cfg.Python.KillGracePeriod.String())
	require.Equal(t, int64(2048), cfg.Python.StdoutLogMaxBytes)
	require.Equal(t, int64(4096), cfg.Python.StderrLogMaxBytes)
	require.Equal(t, "flat", cfg.Python.Step15DefaultRetrievalMode)
	require.Equal(t, "prompt_v2", cfg.Python.Step15DefaultPromptVersion)
	require.Equal(t, "1-2", cfg.Python.Step15DefaultRows)
	require.True(t, cfg.Python.IngestCommandEnabled)
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
	require.False(t, cfg.Jobs.EventBusEnabled)
	require.Equal(t, "override:events", cfg.Jobs.EventChannel)
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
	cfg.Jobs.EventChannel = ""

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "jobs.worker_concurrency")
	require.Contains(t, err.Error(), "jobs.max_attempts")
	require.Contains(t, err.Error(), "jobs.default_timeout")
	require.Contains(t, err.Error(), "jobs.retry_backoff")
	require.Contains(t, err.Error(), "jobs.event_buffer_size")
	require.Contains(t, err.Error(), "jobs.event_channel")
}

func TestValidateInvalidPythonConfig(t *testing.T) {
	cfg := config.Default()
	cfg.Python.ProjectDir = ""
	cfg.Python.ConfigPath = ""
	cfg.Python.DefaultTimeout = config.NewDuration(0)
	cfg.Python.KillGracePeriod = config.NewDuration(0)
	cfg.Python.StdoutLogMaxBytes = 0
	cfg.Python.StderrLogMaxBytes = 0
	cfg.Python.Step15DefaultRetrievalMode = "bad"
	cfg.Python.Step15DefaultPromptVersion = ""
	cfg.Python.Step15DefaultRows = ""

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "python.project_dir")
	require.Contains(t, err.Error(), "python.config_path")
	require.Contains(t, err.Error(), "python.default_timeout")
	require.Contains(t, err.Error(), "python.kill_grace_period")
	require.Contains(t, err.Error(), "python.stdout_log_max_bytes")
	require.Contains(t, err.Error(), "python.stderr_log_max_bytes")
	require.Contains(t, err.Error(), "python.step15_default_retrieval_mode")
	require.Contains(t, err.Error(), "python.step15_default_prompt_version")
	require.Contains(t, err.Error(), "python.step15_default_rows")
}
