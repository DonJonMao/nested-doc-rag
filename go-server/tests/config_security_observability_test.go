package tests

import (
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/stretchr/testify/require"
)

func TestSecurityObservabilityDefaults(t *testing.T) {
	cfg := config.Default()

	require.True(t, cfg.Observability.MetricsEnabled)
	require.False(t, cfg.Observability.PprofEnabled)
	require.Equal(t, "127.0.0.1:6060", cfg.Observability.PprofAddr)
	require.False(t, cfg.Observability.TracingEnabled)
	require.Equal(t, "gongkan-platform", cfg.Observability.TracingServiceName)
	require.Equal(t, "none", cfg.Observability.TracingExporter)
	require.True(t, cfg.Security.SecurityHeadersEnabled)
	require.True(t, cfg.Security.RateLimitEnabled)
	require.Equal(t, 20, cfg.Security.RateLimitRPS)
	require.Equal(t, 40, cfg.Security.RateLimitBurst)
	require.True(t, cfg.Security.BodyLimitEnabled)
	require.Equal(t, int64(256*1024*1024), cfg.Security.MaxBodySize.Bytes)
	require.False(t, cfg.Security.HSTSEnabled)
	require.Equal(t, 30*time.Second, cfg.Operations.GracefulShutdownTimeout.Duration)
}

func TestSecurityObservabilityConfigExampleLoaded(t *testing.T) {
	cfg, err := config.Load("../configs/config.example.yaml")

	require.NoError(t, err)
	require.True(t, cfg.Observability.MetricsEnabled)
	require.False(t, cfg.Observability.PprofEnabled)
	require.True(t, cfg.Security.RateLimitEnabled)
	require.True(t, cfg.Security.BodyLimitEnabled)
	require.Greater(t, cfg.Security.MaxBodySize.Bytes, int64(0))
	require.Greater(t, cfg.Operations.GracefulShutdownTimeout.Duration, time.Duration(0))
}

func TestSecurityObservabilityEnvOverride(t *testing.T) {
	t.Setenv("GONGKAN_OBSERVABILITY_METRICS_ENABLED", "false")
	t.Setenv("GONGKAN_OBSERVABILITY_PPROF_ENABLED", "true")
	t.Setenv("GONGKAN_OBSERVABILITY_PPROF_ADDR", "127.0.0.1:6061")
	t.Setenv("GONGKAN_OBSERVABILITY_TRACING_ENABLED", "true")
	t.Setenv("GONGKAN_OBSERVABILITY_TRACING_EXPORTER", "otlp")
	t.Setenv("GONGKAN_OBSERVABILITY_OTLP_ENDPOINT", "localhost:4317")
	t.Setenv("GONGKAN_SECURITY_RATE_LIMIT_ENABLED", "false")
	t.Setenv("GONGKAN_SECURITY_RATE_LIMIT_RPS", "7")
	t.Setenv("GONGKAN_SECURITY_RATE_LIMIT_BURST", "9")
	t.Setenv("GONGKAN_SECURITY_MAX_BODY_SIZE", "2MB")
	t.Setenv("GONGKAN_SECURITY_HSTS_ENABLED", "true")
	t.Setenv("GONGKAN_OPERATIONS_GRACEFUL_SHUTDOWN_TIMEOUT", "45s")
	cfg := config.Default()

	err := config.ApplyEnvOverrides(cfg)

	require.NoError(t, err)
	require.False(t, cfg.Observability.MetricsEnabled)
	require.True(t, cfg.Observability.PprofEnabled)
	require.Equal(t, "127.0.0.1:6061", cfg.Observability.PprofAddr)
	require.True(t, cfg.Observability.TracingEnabled)
	require.Equal(t, "otlp", cfg.Observability.TracingExporter)
	require.Equal(t, "localhost:4317", cfg.Observability.OTLPEndpoint)
	require.False(t, cfg.Security.RateLimitEnabled)
	require.Equal(t, 7, cfg.Security.RateLimitRPS)
	require.Equal(t, 9, cfg.Security.RateLimitBurst)
	require.Equal(t, int64(2*1024*1024), cfg.Security.MaxBodySize.Bytes)
	require.True(t, cfg.Security.HSTSEnabled)
	require.Equal(t, 45*time.Second, cfg.Operations.GracefulShutdownTimeout.Duration)
}

func TestValidateInvalidRateLimitRejected(t *testing.T) {
	cfg := config.Default()
	cfg.Security.RateLimitEnabled = true
	cfg.Security.RateLimitRPS = 0

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "security.rate_limit_rps")
}

func TestValidateInvalidBodyLimitRejected(t *testing.T) {
	cfg := config.Default()
	cfg.Security.MaxBodySize = config.NewByteSize(0)

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "security.max_body_size")
}

func TestValidateInvalidTracingExporterRejected(t *testing.T) {
	cfg := config.Default()
	cfg.Observability.TracingExporter = "bad"

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "observability.tracing_exporter")
}

func TestValidateInvalidSecurityObservability(t *testing.T) {
	cfg := config.Default()
	cfg.Security.RateLimitRPS = 0
	cfg.Security.RateLimitBurst = 0
	cfg.Security.MaxBodySize = config.NewByteSize(0)
	cfg.Observability.PprofEnabled = true
	cfg.Observability.PprofAddr = ""
	cfg.Observability.TracingExporter = "invalid"
	cfg.Operations.GracefulShutdownTimeout = config.NewDuration(0)

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "security.rate_limit_rps")
	require.Contains(t, err.Error(), "security.rate_limit_burst")
	require.Contains(t, err.Error(), "security.max_body_size")
	require.Contains(t, err.Error(), "observability.pprof_addr")
	require.Contains(t, err.Error(), "observability.tracing_exporter")
	require.Contains(t, err.Error(), "operations.graceful_shutdown_timeout")
}

func TestValidateOTLPEndpointRequired(t *testing.T) {
	cfg := config.Default()
	cfg.Observability.TracingExporter = "otlp"
	cfg.Observability.OTLPEndpoint = ""

	err := config.Validate(cfg)

	require.Error(t, err)
	require.Contains(t, err.Error(), "observability.otlp_endpoint")
}
