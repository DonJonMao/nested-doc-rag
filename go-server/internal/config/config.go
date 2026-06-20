package config

import (
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

const DefaultConfigPath = "configs/config.example.yaml"

type Config struct {
	Server        ServerConfig        `yaml:"server"`
	Database      DatabaseConfig      `yaml:"database"`
	Redis         RedisConfig         `yaml:"redis"`
	Storage       StorageConfig       `yaml:"storage"`
	Auth          AuthConfig          `yaml:"auth"`
	Files         FilesConfig         `yaml:"files"`
	Artifacts     ArtifactsConfig     `yaml:"artifacts"`
	Python        PythonConfig        `yaml:"python"`
	Jobs          JobsConfig          `yaml:"jobs"`
	CORS          CORSConfig          `yaml:"cors"`
	Logging       LoggingConfig       `yaml:"logging"`
	Observability ObservabilityConfig `yaml:"observability"`
	Security      SecurityConfig      `yaml:"security"`
	Operations    OperationsConfig    `yaml:"operations"`
}

type ServerConfig struct {
	Addr            string   `yaml:"addr"`
	ReadTimeout     Duration `yaml:"read_timeout"`
	WriteTimeout    Duration `yaml:"write_timeout"`
	IdleTimeout     Duration `yaml:"idle_timeout"`
	ShutdownTimeout Duration `yaml:"shutdown_timeout"`
}

type DatabaseConfig struct {
	DSN             string   `yaml:"dsn"`
	MaxOpenConns    int32    `yaml:"max_open_conns"`
	MaxIdleConns    int32    `yaml:"max_idle_conns"`
	MaxConnLifetime Duration `yaml:"max_conn_lifetime"`
}

type RedisConfig struct {
	Addr     string `yaml:"addr"`
	Password string `yaml:"password"`
	DB       int    `yaml:"db"`
}

type StorageConfig struct {
	Type     string      `yaml:"type"`
	LocalDir string      `yaml:"local_dir"`
	MinIO    MinIOConfig `yaml:"minio"`
}

type MinIOConfig struct {
	Endpoint  string `yaml:"endpoint"`
	AccessKey string `yaml:"access_key"`
	SecretKey string `yaml:"secret_key"`
	Bucket    string `yaml:"bucket"`
	UseSSL    bool   `yaml:"use_ssl"`
}

type AuthConfig struct {
	AccessTokenTTL  Duration             `yaml:"access_token_ttl"`
	RefreshTokenTTL Duration             `yaml:"refresh_token_ttl"`
	JWTSecretEnv    string               `yaml:"jwt_secret_env"`
	BootstrapAdmin  BootstrapAdminConfig `yaml:"bootstrap_admin"`
}

type BootstrapAdminConfig struct {
	Enabled     bool   `yaml:"enabled"`
	Username    string `yaml:"username"`
	PasswordEnv string `yaml:"password_env"`
}

type FilesConfig struct {
	MaxUploadSize            ByteSize `yaml:"max_upload_size"`
	TempDir                  string   `yaml:"temp_dir"`
	AllowedExtensions        []string `yaml:"allowed_extensions"`
	AllowedMIMETypes         []string `yaml:"allowed_mime_types"`
	DeleteObjectOnSoftDelete bool     `yaml:"delete_object_on_soft_delete"`
}

type ArtifactsConfig struct {
	AllowPresignDownload bool     `yaml:"allow_presign_download"`
	DownloadMode         string   `yaml:"download_mode"`
	DefaultPresignTTL    Duration `yaml:"default_presign_ttl"`
}

type PythonConfig struct {
	Executable                 string   `yaml:"executable"`
	ProjectDir                 string   `yaml:"project_dir"`
	ConfigPath                 string   `yaml:"config_path"`
	DefaultTimeout             Duration `yaml:"default_timeout"`
	ArtifactValidationEnabled  bool     `yaml:"artifact_validation_enabled"`
	KillGracePeriod            Duration `yaml:"kill_grace_period"`
	StdoutLogMaxBytes          int64    `yaml:"stdout_log_max_bytes"`
	StderrLogMaxBytes          int64    `yaml:"stderr_log_max_bytes"`
	Step15DefaultRetrievalMode string   `yaml:"step15_default_retrieval_mode"`
	Step15DefaultPromptVersion string   `yaml:"step15_default_prompt_version"`
	Step15DefaultRows          string   `yaml:"step15_default_rows"`
	IngestCommandEnabled       bool     `yaml:"ingest_command_enabled"`
}

type JobsConfig struct {
	FillConcurrency      int      `yaml:"fill_concurrency"`
	IngestionConcurrency int      `yaml:"ingestion_concurrency"`
	MaxPythonProcesses   int      `yaml:"max_python_processes"`
	RedisNamespace       string   `yaml:"redis_namespace"`
	WorkerConcurrency    int      `yaml:"worker_concurrency"`
	DefaultTimeout       Duration `yaml:"default_timeout"`
	MaxAttempts          int      `yaml:"max_attempts"`
	RetryBackoff         Duration `yaml:"retry_backoff"`
	HeartbeatInterval    Duration `yaml:"heartbeat_interval"`
	EventBufferSize      int      `yaml:"event_buffer_size"`
	EnableNoopJob        bool     `yaml:"enable_noop_job"`
	EventBusEnabled      bool     `yaml:"event_bus_enabled"`
	EventChannel         string   `yaml:"event_channel"`
}

type CORSConfig struct {
	AllowedOrigins   []string `yaml:"allowed_origins"`
	AllowedMethods   []string `yaml:"allowed_methods"`
	AllowedHeaders   []string `yaml:"allowed_headers"`
	AllowCredentials bool     `yaml:"allow_credentials"`
}

type LoggingConfig struct {
	Level       string `yaml:"level"`
	Encoding    string `yaml:"encoding"`
	Development bool   `yaml:"development"`
}

type ObservabilityConfig struct {
	MetricsEnabled     bool   `yaml:"metrics_enabled"`
	PprofEnabled       bool   `yaml:"pprof_enabled"`
	PprofAddr          string `yaml:"pprof_addr"`
	TracingEnabled     bool   `yaml:"tracing_enabled"`
	TracingServiceName string `yaml:"tracing_service_name"`
	TracingExporter    string `yaml:"tracing_exporter"`
	OTLPEndpoint       string `yaml:"otlp_endpoint"`
	LogRequestBody     bool   `yaml:"log_request_body"`
	LogResponseBody    bool   `yaml:"log_response_body"`
}

type SecurityConfig struct {
	SecurityHeadersEnabled bool     `yaml:"security_headers_enabled"`
	RateLimitEnabled       bool     `yaml:"rate_limit_enabled"`
	RateLimitRPS           int      `yaml:"rate_limit_rps"`
	RateLimitBurst         int      `yaml:"rate_limit_burst"`
	BodyLimitEnabled       bool     `yaml:"body_limit_enabled"`
	MaxBodySize            ByteSize `yaml:"max_body_size"`
	TrustedProxies         []string `yaml:"trusted_proxies"`
	CORSAllowCredentials   bool     `yaml:"cors_allow_credentials"`
	HSTSEnabled            bool     `yaml:"hsts_enabled"`
	HSTSMaxAge             Duration `yaml:"hsts_max_age"`
}

type OperationsConfig struct {
	GracefulShutdownTimeout Duration `yaml:"graceful_shutdown_timeout"`
	DiagnosticsEnabled      bool     `yaml:"diagnostics_enabled"`
	ExposeBuildInfo         bool     `yaml:"expose_build_info"`
}

func Load(path string) (*Config, error) {
	if strings.TrimSpace(path) == "" {
		path = DefaultConfigPath
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config %q: %w", path, err)
	}
	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parse config %q: %w", path, err)
	}
	if err := ApplyEnvOverrides(&cfg); err != nil {
		return nil, err
	}
	if err := Validate(&cfg); err != nil {
		return nil, err
	}
	return &cfg, nil
}

func Validate(cfg *Config) error {
	if cfg == nil {
		return errors.New("config is nil")
	}
	var problems []string
	if strings.TrimSpace(cfg.Server.Addr) == "" {
		problems = append(problems, "server.addr is required")
	}
	if strings.TrimSpace(cfg.Database.DSN) == "" {
		problems = append(problems, "database.dsn is required")
	}
	if strings.TrimSpace(cfg.Redis.Addr) == "" {
		problems = append(problems, "redis.addr is required")
	}
	switch cfg.Storage.Type {
	case "local":
		if strings.TrimSpace(cfg.Storage.LocalDir) == "" {
			problems = append(problems, "storage.local_dir is required when storage.type=local")
		}
	case "minio":
		if strings.TrimSpace(cfg.Storage.MinIO.Endpoint) == "" {
			problems = append(problems, "storage.minio.endpoint is required when storage.type=minio")
		}
		if strings.TrimSpace(cfg.Storage.MinIO.Bucket) == "" {
			problems = append(problems, "storage.minio.bucket is required when storage.type=minio")
		}
	default:
		problems = append(problems, "storage.type must be local or minio")
	}
	if strings.TrimSpace(cfg.Python.Executable) == "" {
		problems = append(problems, "python.executable is required")
	}
	if strings.TrimSpace(cfg.Python.ProjectDir) == "" {
		problems = append(problems, "python.project_dir is required")
	}
	if strings.TrimSpace(cfg.Python.ConfigPath) == "" {
		problems = append(problems, "python.config_path is required")
	}
	if cfg.Python.DefaultTimeout.Duration <= 0 {
		problems = append(problems, "python.default_timeout must be greater than 0")
	}
	if cfg.Python.KillGracePeriod.Duration <= 0 {
		problems = append(problems, "python.kill_grace_period must be greater than 0")
	}
	if cfg.Python.StdoutLogMaxBytes <= 0 {
		problems = append(problems, "python.stdout_log_max_bytes must be greater than 0")
	}
	if cfg.Python.StderrLogMaxBytes <= 0 {
		problems = append(problems, "python.stderr_log_max_bytes must be greater than 0")
	}
	switch strings.TrimSpace(cfg.Python.Step15DefaultRetrievalMode) {
	case "flat", "layered":
	default:
		problems = append(problems, "python.step15_default_retrieval_mode must be flat or layered")
	}
	if strings.TrimSpace(cfg.Python.Step15DefaultPromptVersion) == "" {
		problems = append(problems, "python.step15_default_prompt_version is required")
	}
	if strings.TrimSpace(cfg.Python.Step15DefaultRows) == "" {
		problems = append(problems, "python.step15_default_rows is required")
	}
	if cfg.Files.MaxUploadSize.Bytes <= 0 {
		problems = append(problems, "files.max_upload_size must be greater than 0")
	}
	if strings.TrimSpace(cfg.Files.TempDir) == "" {
		problems = append(problems, "files.temp_dir is required")
	}
	if len(cfg.Files.AllowedExtensions) == 0 {
		problems = append(problems, "files.allowed_extensions is required")
	}
	switch cfg.Artifacts.DownloadMode {
	case "proxy", "presign":
	default:
		problems = append(problems, "artifacts.download_mode must be proxy or presign")
	}
	if cfg.Jobs.FillConcurrency <= 0 {
		problems = append(problems, "jobs.fill_concurrency must be greater than 0")
	}
	if cfg.Jobs.IngestionConcurrency <= 0 {
		problems = append(problems, "jobs.ingestion_concurrency must be greater than 0")
	}
	if cfg.Jobs.MaxPythonProcesses <= 0 {
		problems = append(problems, "jobs.max_python_processes must be greater than 0")
	}
	if strings.TrimSpace(cfg.Jobs.RedisNamespace) == "" {
		problems = append(problems, "jobs.redis_namespace is required")
	}
	if cfg.Jobs.WorkerConcurrency <= 0 {
		problems = append(problems, "jobs.worker_concurrency must be greater than 0")
	}
	if cfg.Jobs.DefaultTimeout.Duration <= 0 {
		problems = append(problems, "jobs.default_timeout must be greater than 0")
	}
	if cfg.Jobs.MaxAttempts <= 0 {
		problems = append(problems, "jobs.max_attempts must be greater than 0")
	}
	if cfg.Jobs.RetryBackoff.Duration <= 0 {
		problems = append(problems, "jobs.retry_backoff must be greater than 0")
	}
	if cfg.Jobs.HeartbeatInterval.Duration <= 0 {
		problems = append(problems, "jobs.heartbeat_interval must be greater than 0")
	}
	if cfg.Jobs.EventBufferSize <= 0 {
		problems = append(problems, "jobs.event_buffer_size must be greater than 0")
	}
	if cfg.Jobs.EventBusEnabled && strings.TrimSpace(cfg.Jobs.EventChannel) == "" {
		problems = append(problems, "jobs.event_channel is required when jobs.event_bus_enabled=true")
	}
	if cfg.Security.RateLimitEnabled && cfg.Security.RateLimitRPS <= 0 {
		problems = append(problems, "security.rate_limit_rps must be greater than 0 when rate limit is enabled")
	}
	if cfg.Security.RateLimitEnabled && cfg.Security.RateLimitBurst <= 0 {
		problems = append(problems, "security.rate_limit_burst must be greater than 0 when rate limit is enabled")
	}
	if cfg.Security.MaxBodySize.Bytes <= 0 {
		problems = append(problems, "security.max_body_size must be greater than 0")
	}
	if cfg.Observability.PprofEnabled && strings.TrimSpace(cfg.Observability.PprofAddr) == "" {
		problems = append(problems, "observability.pprof_addr is required when pprof is enabled")
	}
	switch strings.TrimSpace(cfg.Observability.TracingExporter) {
	case "", "none", "stdout", "otlp":
	default:
		problems = append(problems, "observability.tracing_exporter must be none, stdout, or otlp")
	}
	if strings.TrimSpace(cfg.Observability.TracingExporter) == "otlp" && strings.TrimSpace(cfg.Observability.OTLPEndpoint) == "" {
		problems = append(problems, "observability.otlp_endpoint is required when tracing_exporter=otlp")
	}
	if cfg.Operations.GracefulShutdownTimeout.Duration <= 0 {
		problems = append(problems, "operations.graceful_shutdown_timeout must be greater than 0")
	}
	if len(problems) > 0 {
		return fmt.Errorf("invalid config: %s", strings.Join(problems, "; "))
	}
	return nil
}

func Default() *Config {
	return &Config{
		Server: ServerConfig{
			Addr:            ":8080",
			ReadTimeout:     NewDuration(10 * time.Second),
			WriteTimeout:    NewDuration(30 * time.Second),
			IdleTimeout:     NewDuration(60 * time.Second),
			ShutdownTimeout: NewDuration(15 * time.Second),
		},
		Database: DatabaseConfig{
			DSN:             "postgres://gongkan:gongkan@localhost:5432/gongkan?sslmode=disable",
			MaxOpenConns:    20,
			MaxIdleConns:    10,
			MaxConnLifetime: NewDuration(time.Hour),
		},
		Redis: RedisConfig{Addr: "localhost:6379"},
		Storage: StorageConfig{
			Type:     "minio",
			LocalDir: "./runtime/storage",
			MinIO: MinIOConfig{
				Endpoint:  "localhost:9000",
				AccessKey: "minioadmin",
				SecretKey: "minioadmin",
				Bucket:    "gongkan-platform",
			},
		},
		Auth: AuthConfig{
			AccessTokenTTL:  NewDuration(30 * time.Minute),
			RefreshTokenTTL: NewDuration(168 * time.Hour),
			JWTSecretEnv:    "GONGKAN_JWT_SECRET",
			BootstrapAdmin: BootstrapAdminConfig{
				Enabled:     true,
				Username:    "admin",
				PasswordEnv: "GONGKAN_BOOTSTRAP_ADMIN_PASSWORD",
			},
		},
		Files: FilesConfig{
			MaxUploadSize: NewByteSize(200 * 1024 * 1024),
			TempDir:       "./runtime/tmp/uploads",
			AllowedExtensions: []string{
				".xlsx", ".xlsm", ".docx", ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg",
			},
			AllowedMIMETypes: []string{
				"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
				"application/vnd.ms-excel.sheet.macroEnabled.12",
				"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
				"text/plain",
				"text/markdown",
				"text/csv",
				"image/png",
				"image/jpeg",
				"application/octet-stream",
			},
		},
		Artifacts: ArtifactsConfig{
			AllowPresignDownload: false,
			DownloadMode:         "proxy",
			DefaultPresignTTL:    NewDuration(15 * time.Minute),
		},
		Python: PythonConfig{
			Executable:                 "python",
			ProjectDir:                 "../",
			ConfigPath:                 "config/local.yaml",
			DefaultTimeout:             NewDuration(2 * time.Hour),
			ArtifactValidationEnabled:  true,
			KillGracePeriod:            NewDuration(10 * time.Second),
			StdoutLogMaxBytes:          1024 * 1024,
			StderrLogMaxBytes:          1024 * 1024,
			Step15DefaultRetrievalMode: "layered",
			Step15DefaultPromptVersion: "step15_compat",
			Step15DefaultRows:          "4-144",
			IngestCommandEnabled:       true,
		},
		Jobs: JobsConfig{
			FillConcurrency:      1,
			IngestionConcurrency: 1,
			MaxPythonProcesses:   1,
			RedisNamespace:       "gongkan",
			WorkerConcurrency:    4,
			DefaultTimeout:       NewDuration(2 * time.Hour),
			MaxAttempts:          3,
			RetryBackoff:         NewDuration(30 * time.Second),
			HeartbeatInterval:    NewDuration(10 * time.Second),
			EventBufferSize:      256,
			EnableNoopJob:        false,
			EventBusEnabled:      true,
			EventChannel:         "gongkan:run_events",
		},
		CORS: CORSConfig{
			AllowedOrigins: []string{"http://localhost:3000", "http://localhost:5173"},
			AllowedMethods: []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
			AllowedHeaders: []string{"Authorization", "Content-Type", "X-Request-ID"},
		},
		Logging: LoggingConfig{Level: "info", Encoding: "json", Development: true},
		Observability: ObservabilityConfig{
			MetricsEnabled:     true,
			PprofEnabled:       false,
			PprofAddr:          "127.0.0.1:6060",
			TracingEnabled:     false,
			TracingServiceName: "gongkan-platform",
			TracingExporter:    "none",
		},
		Security: SecurityConfig{
			SecurityHeadersEnabled: true,
			RateLimitEnabled:       true,
			RateLimitRPS:           20,
			RateLimitBurst:         40,
			BodyLimitEnabled:       true,
			MaxBodySize:            NewByteSize(256 * 1024 * 1024),
			HSTSEnabled:            false,
			HSTSMaxAge:             NewDuration(720 * time.Hour),
		},
		Operations: OperationsConfig{
			GracefulShutdownTimeout: NewDuration(30 * time.Second),
			DiagnosticsEnabled:      true,
			ExposeBuildInfo:         true,
		},
	}
}
