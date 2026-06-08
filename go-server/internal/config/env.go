package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

func ApplyEnvOverrides(cfg *Config) error {
	var errors []string
	setString(&cfg.Server.Addr, "GONGKAN_SERVER_ADDR")
	setString(&cfg.Database.DSN, "GONGKAN_DATABASE_DSN")
	setInt32(&cfg.Database.MaxOpenConns, "GONGKAN_DATABASE_MAX_OPEN_CONNS", &errors)
	setInt32(&cfg.Database.MaxIdleConns, "GONGKAN_DATABASE_MAX_IDLE_CONNS", &errors)
	setDuration(&cfg.Database.MaxConnLifetime, "GONGKAN_DATABASE_MAX_CONN_LIFETIME", &errors)
	setString(&cfg.Redis.Addr, "GONGKAN_REDIS_ADDR")
	setString(&cfg.Redis.Password, "GONGKAN_REDIS_PASSWORD")
	setInt(&cfg.Redis.DB, "GONGKAN_REDIS_DB", &errors)
	setString(&cfg.Storage.Type, "GONGKAN_STORAGE_TYPE")
	setString(&cfg.Storage.LocalDir, "GONGKAN_STORAGE_LOCAL_DIR")
	setString(&cfg.Storage.MinIO.Endpoint, "GONGKAN_MINIO_ENDPOINT")
	setString(&cfg.Storage.MinIO.AccessKey, "GONGKAN_MINIO_ACCESS_KEY")
	setString(&cfg.Storage.MinIO.SecretKey, "GONGKAN_MINIO_SECRET_KEY")
	setString(&cfg.Storage.MinIO.Bucket, "GONGKAN_MINIO_BUCKET")
	setBool(&cfg.Storage.MinIO.UseSSL, "GONGKAN_MINIO_USE_SSL", &errors)
	setString(&cfg.Auth.JWTSecretEnv, "GONGKAN_JWT_SECRET_ENV")
	setString(&cfg.Auth.JWTSecretEnv, "GONGKAN_AUTH_JWT_SECRET_ENV")
	setString(&cfg.Python.Executable, "GONGKAN_PYTHON_EXECUTABLE")
	setString(&cfg.Python.ProjectDir, "GONGKAN_PYTHON_PROJECT_DIR")
	setString(&cfg.Python.ConfigPath, "GONGKAN_PYTHON_CONFIG_PATH")
	setDuration(&cfg.Python.DefaultTimeout, "GONGKAN_PYTHON_DEFAULT_TIMEOUT", &errors)
	setInt(&cfg.Jobs.FillConcurrency, "GONGKAN_JOBS_FILL_CONCURRENCY", &errors)
	setInt(&cfg.Jobs.IngestionConcurrency, "GONGKAN_JOBS_INGESTION_CONCURRENCY", &errors)
	setInt(&cfg.Jobs.MaxPythonProcesses, "GONGKAN_JOBS_MAX_PYTHON_PROCESSES", &errors)
	setStringSlice(&cfg.CORS.AllowedOrigins, "GONGKAN_CORS_ALLOWED_ORIGINS")
	setStringSlice(&cfg.CORS.AllowedMethods, "GONGKAN_CORS_ALLOWED_METHODS")
	setStringSlice(&cfg.CORS.AllowedHeaders, "GONGKAN_CORS_ALLOWED_HEADERS")
	setString(&cfg.Logging.Level, "GONGKAN_LOGGING_LEVEL")
	setString(&cfg.Logging.Encoding, "GONGKAN_LOGGING_ENCODING")
	setBool(&cfg.Logging.Development, "GONGKAN_LOGGING_DEVELOPMENT", &errors)
	if len(errors) > 0 {
		return fmt.Errorf("apply environment overrides: %s", strings.Join(errors, "; "))
	}
	return nil
}

func setString(target *string, name string) {
	if value, ok := os.LookupEnv(name); ok {
		*target = value
	}
}

func setInt(target *int, name string, errors *[]string) {
	if value, ok := os.LookupEnv(name); ok {
		parsed, err := strconv.Atoi(value)
		if err != nil {
			*errors = append(*errors, fmt.Sprintf("%s must be an integer", name))
			return
		}
		*target = parsed
	}
}

func setInt32(target *int32, name string, errors *[]string) {
	if value, ok := os.LookupEnv(name); ok {
		parsed, err := strconv.ParseInt(value, 10, 32)
		if err != nil {
			*errors = append(*errors, fmt.Sprintf("%s must be an int32", name))
			return
		}
		*target = int32(parsed)
	}
}

func setBool(target *bool, name string, errors *[]string) {
	if value, ok := os.LookupEnv(name); ok {
		parsed, err := strconv.ParseBool(value)
		if err != nil {
			*errors = append(*errors, fmt.Sprintf("%s must be a boolean", name))
			return
		}
		*target = parsed
	}
}

func setDuration(target *Duration, name string, errors *[]string) {
	if value, ok := os.LookupEnv(name); ok {
		parsed, err := time.ParseDuration(value)
		if err != nil {
			*errors = append(*errors, fmt.Sprintf("%s must be a duration", name))
			return
		}
		*target = NewDuration(parsed)
	}
}

func setStringSlice(target *[]string, name string) {
	if value, ok := os.LookupEnv(name); ok {
		var output []string
		for _, part := range strings.Split(value, ",") {
			item := strings.TrimSpace(part)
			if item != "" {
				output = append(output, item)
			}
		}
		*target = output
	}
}
