package modelgateway

import (
	"context"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
)

const (
	KindChat      = "chat"
	KindEmbedding = "embedding"
	KindRerank    = "rerank"

	HeaderInternalToken = "X-NDR-Model-Gateway-Token"
	HeaderRequestID     = "X-NDR-Request-ID"
	HeaderRunID         = "X-NDR-Run-ID"
	HeaderFieldID       = "X-NDR-Field-ID"
	HeaderJobID         = "X-NDR-Job-ID"
	HeaderUserID        = "X-NDR-User-ID"
	HeaderWorkspaceID   = "X-NDR-Workspace-ID"
	HeaderModelKind     = "X-NDR-Model-Kind"
	HeaderModelPurpose  = "X-NDR-Model-Purpose"
)

const (
	CodeUnauthorized        = "model_gateway_unauthorized"
	CodeForbidden           = "model_gateway_forbidden"
	CodeDisabled            = "model_gateway_disabled"
	CodeInvalidRequest      = "model_gateway_invalid_request"
	CodeBodyTooLarge        = "model_gateway_body_too_large"
	CodeQueueFull           = "model_gateway_queue_full"
	CodeQueueTimeout        = "model_gateway_queue_timeout"
	CodeCircuitOpen         = "model_gateway_circuit_open"
	CodeStreamNotSupported  = "model_gateway_stream_not_supported"
	CodeUpstreamTimeout     = "upstream_timeout"
	CodeUpstreamRateLimited = "upstream_rate_limited"
	CodeUpstreamFailed      = "upstream_failed"
	CodeResponseTooLarge    = "upstream_response_too_large"
	CodeContextCanceled     = "model_gateway_request_canceled"
)

type Metadata struct {
	RequestID   string
	RunID       string
	FieldID     string
	JobID       string
	UserID      string
	WorkspaceID string
	ModelKind   string
	Purpose     string
	BodySHA256  string
}

type KindStats struct {
	Enabled      bool   `json:"enabled"`
	Inflight     int64  `json:"inflight"`
	QueueDepth   int    `json:"queue_depth"`
	SuccessTotal int64  `json:"success_total"`
	ErrorTotal   int64  `json:"error_total"`
	RetryTotal   int64  `json:"retry_total"`
	CircuitState string `json:"circuit_state"`
	QueueFull    int64  `json:"queue_full_total"`
	QueueTimeout int64  `json:"queue_timeout_total"`
}

type Stats map[string]KindStats

type upstreamResult struct {
	StatusCode int
	Header     map[string]string
	Body       []byte
	Latency    time.Duration
	Attempts   int
	ErrorCode  string
	Err        error
}

type taskResult struct {
	Result upstreamResult
	Err    *GatewayError
}

type requestTask struct {
	metadata  Metadata
	body      []byte
	queuedAt  time.Time
	context   context.Context
	resultCh  chan taskResult
	bodyBytes int64
}

type KindRuntimeConfig struct {
	Kind                 string
	Enabled              bool
	UpstreamURL          string
	APIKeyEnv            string
	MaxConcurrency       int
	MaxQueueSize         int
	QPS                  int
	RPM                  int
	PerRunMaxInflight    int
	Timeout              time.Duration
	RequestTimeout       time.Duration
	QueueTimeout         time.Duration
	MaxResponseBodyBytes int64
	RetryMaxAttempts     int
	RetryBaseDelay       time.Duration
	RetryMaxDelay        time.Duration
	CircuitThreshold     int
	CircuitOpenFor       time.Duration
}

func runtimeKindConfig(kind string, cfg config.ModelGatewayKindConfig, defaults config.ModelGatewayDefaults) KindRuntimeConfig {
	timeoutSeconds := cfg.TimeoutSeconds
	if timeoutSeconds <= 0 {
		timeoutSeconds = defaults.RequestTimeoutSeconds
	}
	return KindRuntimeConfig{
		Kind:                 kind,
		Enabled:              cfg.Enabled,
		UpstreamURL:          cfg.UpstreamURL,
		APIKeyEnv:            cfg.APIKeyEnv,
		MaxConcurrency:       cfg.MaxConcurrency,
		MaxQueueSize:         cfg.MaxQueueSize,
		QPS:                  cfg.QPS,
		RPM:                  cfg.RPM,
		PerRunMaxInflight:    cfg.PerRunMaxInflight,
		Timeout:              time.Duration(timeoutSeconds) * time.Second,
		RequestTimeout:       time.Duration(defaults.RequestTimeoutSeconds) * time.Second,
		QueueTimeout:         time.Duration(defaults.QueueTimeoutSeconds) * time.Second,
		MaxResponseBodyBytes: defaults.MaxResponseBodyBytes,
		RetryMaxAttempts:     defaults.RetryMaxAttempts,
		RetryBaseDelay:       time.Duration(defaults.RetryBaseDelayMillis) * time.Millisecond,
		RetryMaxDelay:        time.Duration(defaults.RetryMaxDelayMillis) * time.Millisecond,
		CircuitThreshold:     defaults.CircuitFailureThreshold,
		CircuitOpenFor:       time.Duration(defaults.CircuitOpenSeconds) * time.Second,
	}
}
