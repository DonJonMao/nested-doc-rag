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
	Server    ServerConfig    `yaml:"server"`
	Database  DatabaseConfig  `yaml:"database"`
	Redis     RedisConfig     `yaml:"redis"`
	Storage   StorageConfig   `yaml:"storage"`
	Auth      AuthConfig      `yaml:"auth"`
	Files     FilesConfig     `yaml:"files"`
	Artifacts ArtifactsConfig `yaml:"artifacts"`
	Python    PythonConfig    `yaml:"python"`
	Jobs      JobsConfig      `yaml:"jobs"`
	CORS      CORSConfig      `yaml:"cors"`
	Logging   LoggingConfig   `yaml:"logging"`
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
	Executable     string   `yaml:"executable"`
	ProjectDir     string   `yaml:"project_dir"`
	ConfigPath     string   `yaml:"config_path"`
	DefaultTimeout Duration `yaml:"default_timeout"`
}

type JobsConfig struct {
	FillConcurrency      int `yaml:"fill_concurrency"`
	IngestionConcurrency int `yaml:"ingestion_concurrency"`
	MaxPythonProcesses   int `yaml:"max_python_processes"`
}

type CORSConfig struct {
	AllowedOrigins []string `yaml:"allowed_origins"`
	AllowedMethods []string `yaml:"allowed_methods"`
	AllowedHeaders []string `yaml:"allowed_headers"`
}

type LoggingConfig struct {
	Level       string `yaml:"level"`
	Encoding    string `yaml:"encoding"`
	Development bool   `yaml:"development"`
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
	if cfg.Files.MaxUploadSize.Bytes <= 0 {
		problems = append(problems, "files.max_upload_size must be greater than 0")
	}
	if strings.TrimSpace(cfg.Files.TempDir) == "" {
		problems = append(problems, "files.temp_dir is required")
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
				".xlsx", ".xls", ".docx", ".png", ".jpg", ".jpeg",
			},
			AllowedMIMETypes: []string{
				"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
				"application/vnd.ms-excel",
				"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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
			Executable:     "python",
			ProjectDir:     "../",
			ConfigPath:     "config/local.yaml",
			DefaultTimeout: NewDuration(2 * time.Hour),
		},
		Jobs: JobsConfig{FillConcurrency: 2, IngestionConcurrency: 2, MaxPythonProcesses: 3},
		CORS: CORSConfig{
			AllowedOrigins: []string{"http://localhost:3000"},
			AllowedMethods: []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
			AllowedHeaders: []string{"Authorization", "Content-Type", "X-Request-ID"},
		},
		Logging: LoggingConfig{Level: "info", Encoding: "json", Development: true},
	}
}
