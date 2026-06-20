package modelgateway

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"io"
	"math/rand"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type Service struct {
	cfg       config.ModelGatewayConfig
	token     string
	logger    *zap.Logger
	kinds     map[string]*kindProxy
	bodyLimit int64
}

func NewService(cfg config.ModelGatewayConfig, logger *zap.Logger) (*Service, error) {
	if logger == nil {
		logger = zap.NewNop()
	}
	if !cfg.Enabled {
		return &Service{cfg: cfg, logger: logger, kinds: map[string]*kindProxy{}}, nil
	}
	token := ""
	if cfg.RequireInternalToken {
		token = os.Getenv(strings.TrimSpace(cfg.InternalTokenEnv))
		if token == "" {
			return nil, errors.New("model gateway internal token env is empty")
		}
	}
	service := &Service{
		cfg:       cfg,
		token:     token,
		logger:    logger,
		kinds:     make(map[string]*kindProxy),
		bodyLimit: cfg.Defaults.MaxRequestBodyBytes,
	}
	for kind, kindCfg := range map[string]config.ModelGatewayKindConfig{
		KindChat:      cfg.Chat,
		KindEmbedding: cfg.Embedding,
		KindRerank:    cfg.Rerank,
	} {
		runtime := runtimeKindConfig(kind, kindCfg, cfg.Defaults)
		service.kinds[kind] = newKindProxy(runtime, logger)
	}
	return service, nil
}

func (s *Service) Enabled() bool {
	return s != nil && s.cfg.Enabled
}

func (s *Service) BodyLimit() int64 {
	if s == nil || s.bodyLimit <= 0 {
		return 10 * 1024 * 1024
	}
	return s.bodyLimit
}

func (s *Service) Authorize(r *http.Request) *GatewayError {
	if s == nil || !s.cfg.RequireInternalToken {
		return nil
	}
	token := tokenFromRequest(r)
	if token == "" {
		return newGatewayError(CodeUnauthorized, "model gateway internal token is required", http.StatusUnauthorized, nil)
	}
	if subtle.ConstantTimeCompare([]byte(token), []byte(s.token)) != 1 {
		return newGatewayError(CodeForbidden, "model gateway internal token is invalid", http.StatusForbidden, nil)
	}
	return nil
}

func tokenFromRequest(r *http.Request) string {
	if r == nil {
		return ""
	}
	if value := strings.TrimSpace(r.Header.Get(HeaderInternalToken)); value != "" {
		return value
	}
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	if strings.HasPrefix(strings.ToLower(auth), "bearer ") {
		return strings.TrimSpace(auth[len("bearer "):])
	}
	return ""
}

func (s *Service) Proxy(ctx context.Context, kind string, metadata Metadata, body []byte) (upstreamResult, *GatewayError) {
	if s == nil || !s.cfg.Enabled {
		return upstreamResult{}, newGatewayError(CodeDisabled, "model gateway is disabled", http.StatusServiceUnavailable, nil)
	}
	proxy := s.kinds[kind]
	if proxy == nil || !proxy.cfg.Enabled {
		return upstreamResult{}, newGatewayError(CodeDisabled, "model gateway kind is disabled", http.StatusServiceUnavailable, nil)
	}
	if metadata.RequestID == "" {
		metadata.RequestID = uuid.NewString()
	}
	if metadata.ModelKind == "" {
		metadata.ModelKind = kind
	}
	if metadata.RunID == "" {
		metadata.RunID = "unknown"
	}
	sum := sha256.Sum256(body)
	metadata.BodySHA256 = hex.EncodeToString(sum[:])
	return proxy.submit(ctx, metadata, body)
}

func (s *Service) Stats() Stats {
	stats := Stats{}
	if s == nil {
		return stats
	}
	for _, kind := range []string{KindChat, KindEmbedding, KindRerank} {
		if proxy := s.kinds[kind]; proxy != nil {
			stats[kind] = proxy.stats()
		}
	}
	return stats
}

type kindProxy struct {
	cfg        KindRuntimeConfig
	logger     *zap.Logger
	queue      chan *requestTask
	breaker    *circuitBreaker
	limiter    *rateLimiter
	perRun     map[string]chan struct{}
	perRunMu   sync.Mutex
	httpClient *http.Client

	inflight     atomic.Int64
	successTotal atomic.Int64
	errorTotal   atomic.Int64
	retryTotal   atomic.Int64
	queueFull    atomic.Int64
	queueTimeout atomic.Int64
}

func newKindProxy(cfg KindRuntimeConfig, logger *zap.Logger) *kindProxy {
	if cfg.MaxConcurrency <= 0 {
		cfg.MaxConcurrency = 1
	}
	if cfg.PerRunMaxInflight <= 0 {
		cfg.PerRunMaxInflight = 1
	}
	if cfg.QueueTimeout <= 0 {
		cfg.QueueTimeout = 300 * time.Second
	}
	p := &kindProxy{
		cfg:        cfg,
		logger:     logger,
		queue:      make(chan *requestTask, cfg.MaxQueueSize),
		breaker:    newCircuitBreaker(cfg.CircuitThreshold, cfg.CircuitOpenFor),
		limiter:    newRateLimiter(cfg.QPS, cfg.RPM),
		perRun:     make(map[string]chan struct{}),
		httpClient: &http.Client{},
	}
	if cfg.Enabled {
		for i := 0; i < cfg.MaxConcurrency; i++ {
			go p.worker()
		}
	}
	return p
}

func (p *kindProxy) submit(ctx context.Context, metadata Metadata, body []byte) (upstreamResult, *GatewayError) {
	if !p.breaker.allow() {
		p.errorTotal.Add(1)
		return upstreamResult{}, newGatewayError(CodeCircuitOpen, "model gateway circuit is open", http.StatusServiceUnavailable, nil)
	}
	task := &requestTask{
		metadata:  metadata,
		body:      append([]byte(nil), body...),
		queuedAt:  time.Now(),
		context:   ctx,
		resultCh:  make(chan taskResult, 1),
		bodyBytes: int64(len(body)),
	}
	select {
	case p.queue <- task:
	default:
		p.queueFull.Add(1)
		p.errorTotal.Add(1)
		return upstreamResult{}, newGatewayError(CodeQueueFull, "model gateway queue is full", http.StatusTooManyRequests, nil)
	}
	select {
	case result := <-task.resultCh:
		return result.Result, result.Err
	case <-ctx.Done():
		p.errorTotal.Add(1)
		return upstreamResult{}, newGatewayError(CodeContextCanceled, "model gateway request was canceled", http.StatusGatewayTimeout, ctx.Err())
	}
}

func (p *kindProxy) worker() {
	for task := range p.queue {
		p.handleTask(task)
	}
}

func (p *kindProxy) handleTask(task *requestTask) {
	queueWait := time.Since(task.queuedAt)
	if queueWait > p.cfg.QueueTimeout {
		p.queueTimeout.Add(1)
		p.errorTotal.Add(1)
		p.trySend(task, taskResult{Err: newGatewayError(CodeQueueTimeout, "model gateway queue wait timed out", http.StatusTooManyRequests, nil)})
		p.logDone(task.metadata, queueWait, 0, 0, 0, CodeQueueTimeout, false, task.bodyBytes, 0)
		return
	}
	if err := task.context.Err(); err != nil {
		p.errorTotal.Add(1)
		p.trySend(task, taskResult{Err: newGatewayError(CodeContextCanceled, "model gateway request was canceled", http.StatusGatewayTimeout, err)})
		return
	}
	p.inflight.Add(1)
	defer p.inflight.Add(-1)
	release, err := p.acquireRun(task.context, task.metadata.RunID)
	if err != nil {
		p.errorTotal.Add(1)
		p.trySend(task, taskResult{Err: newGatewayError(CodeContextCanceled, "model gateway request was canceled", http.StatusGatewayTimeout, err)})
		return
	}
	defer release()
	if err := p.limiter.wait(task.context); err != nil {
		p.errorTotal.Add(1)
		p.trySend(task, taskResult{Err: newGatewayError(CodeContextCanceled, "model gateway request was canceled", http.StatusGatewayTimeout, err)})
		return
	}
	result := p.callUpstreamWithRetry(task.context, task.metadata, task.body)
	success := result.Err == nil && result.StatusCode < 500 && result.StatusCode != http.StatusTooManyRequests
	if success {
		p.successTotal.Add(1)
		p.breaker.recordSuccess()
	} else {
		p.errorTotal.Add(1)
		if isCircuitFailure(result.StatusCode, result.Err) {
			p.breaker.recordFailure()
		}
	}
	p.logDone(
		task.metadata,
		queueWait,
		result.Latency,
		result.Attempts,
		result.StatusCode,
		result.ErrorCode,
		success,
		task.bodyBytes,
		int64(len(result.Body)),
	)
	if result.Err != nil {
		status := http.StatusBadGateway
		code := result.ErrorCode
		if code == "" {
			code = CodeUpstreamFailed
		}
		if code == CodeUpstreamTimeout {
			status = http.StatusGatewayTimeout
		}
		p.trySend(task, taskResult{Result: result, Err: newGatewayError(code, "upstream model service request failed", status, result.Err)})
		return
	}
	p.trySend(task, taskResult{Result: result})
}

func (p *kindProxy) acquireRun(ctx context.Context, runID string) (func(), error) {
	key := strings.TrimSpace(runID)
	if key == "" {
		key = "unknown"
	}
	p.perRunMu.Lock()
	sem := p.perRun[key]
	if sem == nil {
		sem = make(chan struct{}, p.cfg.PerRunMaxInflight)
		p.perRun[key] = sem
	}
	p.perRunMu.Unlock()
	select {
	case sem <- struct{}{}:
		return func() { <-sem }, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (p *kindProxy) callUpstreamWithRetry(ctx context.Context, metadata Metadata, body []byte) upstreamResult {
	attempts := p.cfg.RetryMaxAttempts
	if attempts <= 0 {
		attempts = 1
	}
	var last upstreamResult
	for attempt := 1; attempt <= attempts; attempt++ {
		result := p.callUpstream(ctx, body)
		result.Attempts = attempt
		last = result
		if !shouldRetry(result.StatusCode, result.Err) || attempt >= attempts {
			return result
		}
		p.retryTotal.Add(1)
		delay := p.retryDelay(attempt, result.Header["Retry-After"])
		p.logger.Warn("model gateway upstream retry",
			zap.String("request_id", metadata.RequestID),
			zap.String("run_id", metadata.RunID),
			zap.String("field_id", metadata.FieldID),
			zap.String("job_id", metadata.JobID),
			zap.String("user_id", metadata.UserID),
			zap.String("workspace_id", metadata.WorkspaceID),
			zap.String("model_kind", metadata.ModelKind),
			zap.String("model_purpose", metadata.Purpose),
			zap.Int("attempt_number", attempt),
			zap.Int("status_code", result.StatusCode),
			zap.String("error_code", result.ErrorCode),
			zap.Duration("backoff", delay),
		)
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			last.Err = ctx.Err()
			last.ErrorCode = CodeContextCanceled
			return last
		case <-timer.C:
		}
	}
	return last
}

func (p *kindProxy) callUpstream(ctx context.Context, body []byte) upstreamResult {
	timeout := p.cfg.Timeout
	if timeout <= 0 {
		timeout = p.cfg.RequestTimeout
	}
	if timeout <= 0 {
		timeout = 180 * time.Second
	}
	reqCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	started := time.Now()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, p.cfg.UpstreamURL, bytes.NewReader(body))
	if err != nil {
		return upstreamResult{Err: err, ErrorCode: CodeInvalidRequest, Latency: time.Since(started)}
	}
	req.Header.Set("Content-Type", "application/json")
	if apiKeyEnv := strings.TrimSpace(p.cfg.APIKeyEnv); apiKeyEnv != "" {
		if apiKey := os.Getenv(apiKeyEnv); apiKey != "" {
			req.Header.Set("Authorization", "Bearer "+apiKey)
		}
	}
	resp, err := p.httpClient.Do(req)
	latency := time.Since(started)
	if err != nil {
		return upstreamResult{Err: err, ErrorCode: classifyNetworkError(err), Latency: latency}
	}
	defer resp.Body.Close()
	limit := p.cfg.MaxResponseBodyBytes
	if limit <= 0 {
		limit = 10 * 1024 * 1024
	}
	payload, readErr := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if readErr != nil {
		return upstreamResult{StatusCode: resp.StatusCode, Err: readErr, ErrorCode: CodeUpstreamFailed, Latency: latency}
	}
	if int64(len(payload)) > limit {
		return upstreamResult{StatusCode: http.StatusBadGateway, Err: errors.New("upstream response body too large"), ErrorCode: CodeResponseTooLarge, Latency: latency}
	}
	header := map[string]string{}
	for name, values := range resp.Header {
		if len(values) > 0 {
			header[name] = values[0]
		}
	}
	code := ""
	if resp.StatusCode == http.StatusTooManyRequests {
		code = CodeUpstreamRateLimited
	} else if resp.StatusCode >= 500 {
		code = CodeUpstreamFailed
	}
	return upstreamResult{StatusCode: resp.StatusCode, Header: header, Body: payload, Latency: latency, ErrorCode: code}
}

func classifyNetworkError(err error) string {
	if err == nil {
		return ""
	}
	if errors.Is(err, context.DeadlineExceeded) || strings.Contains(strings.ToLower(err.Error()), "timeout") {
		return CodeUpstreamTimeout
	}
	var netErr net.Error
	if errors.As(err, &netErr) && netErr.Timeout() {
		return CodeUpstreamTimeout
	}
	return CodeUpstreamFailed
}

func shouldRetry(status int, err error) bool {
	if err != nil {
		return true
	}
	switch status {
	case http.StatusTooManyRequests, http.StatusBadGateway, http.StatusServiceUnavailable, http.StatusGatewayTimeout:
		return true
	default:
		return false
	}
}

func isCircuitFailure(status int, err error) bool {
	return shouldRetry(status, err)
}

func (p *kindProxy) retryDelay(attempt int, retryAfter string) time.Duration {
	if retryAfter != "" {
		if seconds, err := strconv.Atoi(strings.TrimSpace(retryAfter)); err == nil {
			delay := time.Duration(seconds) * time.Second
			if delay > 0 && delay <= p.cfg.RetryMaxDelay {
				return delay
			}
		}
		if when, err := http.ParseTime(retryAfter); err == nil {
			delay := time.Until(when)
			if delay > 0 && delay <= p.cfg.RetryMaxDelay {
				return delay
			}
		}
	}
	base := p.cfg.RetryBaseDelay
	if base <= 0 {
		base = 500 * time.Millisecond
	}
	delay := base * time.Duration(1<<max(0, attempt-1))
	if p.cfg.RetryMaxDelay > 0 && delay > p.cfg.RetryMaxDelay {
		delay = p.cfg.RetryMaxDelay
	}
	jitter := time.Duration(rand.Int63n(int64(maxDuration(time.Millisecond, delay/2))))
	return delay/2 + jitter
}

func max(a int, b int) int {
	if a > b {
		return a
	}
	return b
}

func maxDuration(a time.Duration, b time.Duration) time.Duration {
	if a > b {
		return a
	}
	return b
}

func (p *kindProxy) trySend(task *requestTask, result taskResult) {
	select {
	case task.resultCh <- result:
	default:
	}
}

func (p *kindProxy) stats() KindStats {
	return KindStats{
		Enabled:      p.cfg.Enabled,
		Inflight:     p.inflight.Load(),
		QueueDepth:   len(p.queue),
		SuccessTotal: p.successTotal.Load(),
		ErrorTotal:   p.errorTotal.Load(),
		RetryTotal:   p.retryTotal.Load(),
		CircuitState: p.breaker.stateName(),
		QueueFull:    p.queueFull.Load(),
		QueueTimeout: p.queueTimeout.Load(),
	}
}

func (p *kindProxy) logDone(metadata Metadata, queueWait time.Duration, upstreamLatency time.Duration, attempts int, statusCode int, errorCode string, success bool, requestBytes int64, responseBytes int64) {
	p.logger.Info("model gateway request completed",
		zap.String("request_id", metadata.RequestID),
		zap.String("run_id", metadata.RunID),
		zap.String("field_id", metadata.FieldID),
		zap.String("job_id", metadata.JobID),
		zap.String("user_id", metadata.UserID),
		zap.String("workspace_id", metadata.WorkspaceID),
		zap.String("model_kind", metadata.ModelKind),
		zap.String("model_purpose", metadata.Purpose),
		zap.Int64("queue_wait_ms", queueWait.Milliseconds()),
		zap.Int64("upstream_latency_ms", upstreamLatency.Milliseconds()),
		zap.Int("attempt_count", attempts),
		zap.Int("status_code", statusCode),
		zap.String("error_code", errorCode),
		zap.Bool("success", success),
		zap.Int64("request_bytes", requestBytes),
		zap.Int64("response_bytes", responseBytes),
		zap.String("body_sha256", metadata.BodySHA256),
	)
}
