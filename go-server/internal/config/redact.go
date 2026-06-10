package config

func RedactedSummary(cfg Config) map[string]any {
	return redactSensitiveMap(map[string]any{
		"server": map[string]any{
			"addr": cfg.Server.Addr,
		},
		"database": map[string]any{
			"dsn": cfg.Database.DSN,
		},
		"redis": map[string]any{
			"addr":     cfg.Redis.Addr,
			"password": cfg.Redis.Password,
			"db":       cfg.Redis.DB,
		},
		"storage": map[string]any{
			"type": cfg.Storage.Type,
			"minio": map[string]any{
				"endpoint":   cfg.Storage.MinIO.Endpoint,
				"access_key": cfg.Storage.MinIO.AccessKey,
				"secret_key": cfg.Storage.MinIO.SecretKey,
				"bucket":     cfg.Storage.MinIO.Bucket,
				"use_ssl":    cfg.Storage.MinIO.UseSSL,
			},
		},
		"auth": map[string]any{
			"jwt_secret_env": cfg.Auth.JWTSecretEnv,
			"bootstrap_admin": map[string]any{
				"enabled":      cfg.Auth.BootstrapAdmin.Enabled,
				"username":     cfg.Auth.BootstrapAdmin.Username,
				"password_env": cfg.Auth.BootstrapAdmin.PasswordEnv,
			},
		},
		"observability": map[string]any{
			"metrics_enabled":  cfg.Observability.MetricsEnabled,
			"pprof_enabled":    cfg.Observability.PprofEnabled,
			"pprof_addr":       cfg.Observability.PprofAddr,
			"tracing_enabled":  cfg.Observability.TracingEnabled,
			"tracing_exporter": cfg.Observability.TracingExporter,
			"otlp_endpoint":    cfg.Observability.OTLPEndpoint,
		},
		"security": map[string]any{
			"security_headers_enabled": cfg.Security.SecurityHeadersEnabled,
			"rate_limit_enabled":       cfg.Security.RateLimitEnabled,
			"body_limit_enabled":       cfg.Security.BodyLimitEnabled,
			"hsts_enabled":             cfg.Security.HSTSEnabled,
		},
	})
}

func redactSensitiveMap(input map[string]any) map[string]any {
	if input == nil {
		return nil
	}
	out := make(map[string]any, len(input))
	for key, value := range input {
		if isSensitiveConfigKey(key) {
			out[key] = "[REDACTED]"
			continue
		}
		if nested, ok := value.(map[string]any); ok {
			out[key] = redactSensitiveMap(nested)
			continue
		}
		out[key] = value
	}
	return out
}

func isSensitiveConfigKey(key string) bool {
	switch key {
	case "password", "secret_key", "jwt_secret_env", "password_env", "dsn", "otlp_endpoint":
		return true
	default:
		return false
	}
}
