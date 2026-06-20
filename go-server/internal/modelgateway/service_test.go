package modelgateway

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
	"go.uber.org/zap/zaptest/observer"
)

func TestModelGatewayRequiresInternalToken(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()
	server := newGatewayTestServer(t, testGatewayConfig(upstream.URL), zap.NewNop())
	defer server.Close()

	resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "", `{"messages":[]}`)
	require.Equal(t, http.StatusUnauthorized, resp.StatusCode)
	_ = resp.Body.Close()

	resp = postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "bad", `{"messages":[]}`)
	require.Equal(t, http.StatusForbidden, resp.StatusCode)
	_ = resp.Body.Close()
}

func TestModelGatewayChatPassthrough(t *testing.T) {
	t.Setenv("DEEPSEEK_API_KEY", "upstream-secret")
	var gotAuth string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		body, _ := io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"echo":` + string(body) + `}`))
	}))
	defer upstream.Close()
	cfg := testGatewayConfig(upstream.URL)
	server := newGatewayTestServer(t, cfg, zap.NewNop())
	defer server.Close()

	resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"model":"m","messages":[{"role":"user","content":"hello"}]}`)
	defer resp.Body.Close()

	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Equal(t, "Bearer upstream-secret", gotAuth)
	var payload map[string]any
	require.NoError(t, json.NewDecoder(resp.Body).Decode(&payload))
	require.NotNil(t, payload["echo"])
}

func TestModelGatewayEmbeddingPassthrough(t *testing.T) {
	assertKindPassthrough(t, KindEmbedding, "/internal/model-gateway/v1/embeddings", `{"data":[{"index":0,"embedding":[1]}]}`)
}

func TestModelGatewayRerankPassthrough(t *testing.T) {
	assertKindPassthrough(t, KindRerank, "/internal/model-gateway/v1/rerank", `{"results":[{"index":0,"relevance_score":1}]}`)
}

func TestModelGatewayConcurrencyLimit(t *testing.T) {
	var current atomic.Int64
	var maxSeen atomic.Int64
	release := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		now := current.Add(1)
		for {
			old := maxSeen.Load()
			if now <= old || maxSeen.CompareAndSwap(old, now) {
				break
			}
		}
		<-release
		current.Add(-1)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()
	cfg := testGatewayConfig(upstream.URL)
	cfg.Chat.MaxConcurrency = 1
	cfg.Chat.MaxQueueSize = 2
	server := newGatewayTestServer(t, cfg, zap.NewNop())
	defer server.Close()

	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
			_ = resp.Body.Close()
		}()
	}
	require.Eventually(t, func() bool { return current.Load() == 1 }, time.Second, 10*time.Millisecond)
	close(release)
	wg.Wait()
	require.Equal(t, int64(1), maxSeen.Load())
}

func TestModelGatewayPerRunLimit(t *testing.T) {
	var current atomic.Int64
	var maxSeen atomic.Int64
	release := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		now := current.Add(1)
		for {
			old := maxSeen.Load()
			if now <= old || maxSeen.CompareAndSwap(old, now) {
				break
			}
		}
		<-release
		current.Add(-1)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()
	cfg := testGatewayConfig(upstream.URL)
	cfg.Chat.MaxConcurrency = 2
	cfg.Chat.PerRunMaxInflight = 1
	server := newGatewayTestServer(t, cfg, zap.NewNop())
	defer server.Close()

	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			req := newGatewayRequest(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
			req.Header.Set(HeaderRunID, "same-run")
			resp, err := http.DefaultClient.Do(req)
			require.NoError(t, err)
			_ = resp.Body.Close()
		}()
	}
	require.Eventually(t, func() bool { return current.Load() == 1 }, time.Second, 10*time.Millisecond)
	close(release)
	wg.Wait()
	require.Equal(t, int64(1), maxSeen.Load())
}

func TestModelGatewayQueueFull(t *testing.T) {
	release := make(chan struct{})
	upstreamStarted := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case upstreamStarted <- struct{}{}:
		default:
		}
		<-release
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()
	cfg := testGatewayConfig(upstream.URL)
	cfg.Chat.MaxConcurrency = 1
	cfg.Chat.MaxQueueSize = 1
	service := newGatewayTestService(t, cfg, zap.NewNop())
	r := chi.NewRouter()
	NewHandler(service).RegisterRoutes(r)
	server := httptest.NewServer(r)
	defer server.Close()
	defer close(release)

	go func() {
		resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
		_ = resp.Body.Close()
	}()
	<-upstreamStarted
	go func() {
		resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
		_ = resp.Body.Close()
	}()
	require.Eventually(t, func() bool {
		return service.kinds[KindChat].stats().QueueDepth == 1
	}, time.Second, 10*time.Millisecond)
	resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	require.Equal(t, http.StatusTooManyRequests, resp.StatusCode)
	require.Contains(t, string(body), CodeQueueFull)
}

func TestModelGatewayQueueTimeout(t *testing.T) {
	release := make(chan struct{})
	upstreamStarted := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case upstreamStarted <- struct{}{}:
		default:
		}
		<-release
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()
	cfg := testGatewayConfig(upstream.URL)
	cfg.Defaults.QueueTimeoutSeconds = 1
	cfg.Chat.MaxConcurrency = 1
	cfg.Chat.MaxQueueSize = 1
	server := newGatewayTestServer(t, cfg, zap.NewNop())
	defer server.Close()
	released := false
	defer func() {
		if !released {
			close(release)
		}
	}()

	go func() {
		resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
		_ = resp.Body.Close()
	}()
	<-upstreamStarted
	resultCh := make(chan *http.Response, 1)
	go func() {
		resultCh <- postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
	}()
	time.Sleep(1100 * time.Millisecond)
	close(release)
	released = true
	resp := <-resultCh
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	require.Equal(t, http.StatusTooManyRequests, resp.StatusCode)
	require.Contains(t, string(body), CodeQueueTimeout)
}

func TestModelGatewayRetryOn429And503(t *testing.T) {
	var count atomic.Int64
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := count.Add(1)
		if n == 1 {
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = w.Write([]byte(`{"error":"rate"}`))
			return
		}
		if n == 2 {
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = w.Write([]byte(`{"error":"down"}`))
			return
		}
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()
	cfg := testGatewayConfig(upstream.URL)
	cfg.Defaults.RetryBaseDelayMillis = 1
	cfg.Defaults.RetryMaxDelayMillis = 5
	cfg.Defaults.RetryMaxAttempts = 3
	server := newGatewayTestServer(t, cfg, zap.NewNop())
	defer server.Close()

	resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
	defer resp.Body.Close()
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Equal(t, int64(3), count.Load())
}

func TestModelGatewayDoesNotRetryOn400(t *testing.T) {
	var count atomic.Int64
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		count.Add(1)
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"error":"bad"}`))
	}))
	defer upstream.Close()
	cfg := testGatewayConfig(upstream.URL)
	cfg.Defaults.RetryMaxAttempts = 3
	server := newGatewayTestServer(t, cfg, zap.NewNop())
	defer server.Close()

	resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
	defer resp.Body.Close()
	require.Equal(t, http.StatusBadRequest, resp.StatusCode)
	require.Equal(t, int64(1), count.Load())
}

func TestModelGatewayCircuitBreaker(t *testing.T) {
	var count atomic.Int64
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		count.Add(1)
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte(`{"error":"down"}`))
	}))
	defer upstream.Close()
	cfg := testGatewayConfig(upstream.URL)
	cfg.Defaults.RetryMaxAttempts = 1
	cfg.Defaults.CircuitFailureThreshold = 2
	server := newGatewayTestServer(t, cfg, zap.NewNop())
	defer server.Close()

	for i := 0; i < 2; i++ {
		resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
		_ = resp.Body.Close()
		require.Equal(t, http.StatusServiceUnavailable, resp.StatusCode)
	}
	resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	require.Equal(t, http.StatusServiceUnavailable, resp.StatusCode)
	require.Contains(t, string(body), CodeCircuitOpen)
	require.Equal(t, int64(2), count.Load())
}

func TestModelGatewayNoCrossTalkBetweenConcurrentRequests(t *testing.T) {
	aCanReturn := make(chan struct{})
	bReturned := make(chan struct{})
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]string
		_ = json.NewDecoder(r.Body).Decode(&payload)
		marker := payload["question"]
		if marker == "A" {
			<-aCanReturn
		}
		if marker == "B" {
			close(bReturned)
		}
		_, _ = w.Write([]byte(`{"answer":"` + marker + `"}`))
	}))
	defer upstream.Close()
	cfg := testGatewayConfig(upstream.URL)
	cfg.Chat.MaxConcurrency = 2
	server := newGatewayTestServer(t, cfg, zap.NewNop())
	defer server.Close()

	ch := make(chan resultForMarker, 2)
	go func() { ch <- sendMarkedChat(t, server.URL, "A", "field-a") }()
	go func() { ch <- sendMarkedChat(t, server.URL, "B", "field-b") }()
	<-bReturned
	close(aCanReturn)
	first := <-ch
	second := <-ch
	results := map[string]resultForMarker{first.marker: first, second.marker: second}
	require.Contains(t, results["A"].body, `"answer":"A"`)
	require.Contains(t, results["B"].body, `"answer":"B"`)
	require.NotEqual(t, results["A"].requestID, results["B"].requestID)
}

func TestModelGatewayCancellationDoesNotLeakSemaphore(t *testing.T) {
	var calls atomic.Int64
	cfg := testGatewayConfig("http://upstream.test/v1/chat/completions")
	cfg.Chat.MaxConcurrency = 1
	cfg.Chat.MaxQueueSize = 1
	service := newGatewayTestService(t, cfg, zap.NewNop())
	service.kinds[KindChat].httpClient = &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		if calls.Add(1) == 1 {
			<-req.Context().Done()
			return nil, req.Context().Err()
		}
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     make(http.Header),
			Body:       io.NopCloser(strings.NewReader(`{"ok":true}`)),
		}, nil
	})}
	r := chi.NewRouter()
	NewHandler(service).RegisterRoutes(r)
	server := httptest.NewServer(r)
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	req := newGatewayRequest(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
	req = req.WithContext(ctx)
	done := make(chan struct{})
	go func() {
		resp, _ := http.DefaultClient.Do(req)
		if resp != nil {
			_ = resp.Body.Close()
		}
		close(done)
	}()
	time.Sleep(50 * time.Millisecond)
	cancel()
	<-done
	require.Eventually(t, func() bool {
		return service.kinds[KindChat].stats().Inflight == 0
	}, time.Second, 10*time.Millisecond)

	resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[]}`)
	defer resp.Body.Close()
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Eventually(t, func() bool {
		return service.kinds[KindChat].stats().Inflight == 0
	}, time.Second, 10*time.Millisecond)
}

func TestModelGatewayDoesNotLogPromptBody(t *testing.T) {
	core, logs := observer.New(zap.InfoLevel)
	logger := zap.New(core)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()
	server := newGatewayTestServer(t, testGatewayConfig(upstream.URL), logger)
	defer server.Close()

	resp := postGateway(t, server.URL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"messages":[{"content":"SHOULD_NOT_LOG_PROMPT"}]}`)
	_ = resp.Body.Close()

	for _, entry := range logs.All() {
		require.NotContains(t, entry.Message, "SHOULD_NOT_LOG_PROMPT")
		for _, field := range entry.Context {
			require.NotContains(t, field.String, "SHOULD_NOT_LOG_PROMPT")
		}
	}
}

func TestModelGatewayStatsEndpointRequiresToken(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer upstream.Close()
	server := newGatewayTestServer(t, testGatewayConfig(upstream.URL), zap.NewNop())
	defer server.Close()

	resp, err := http.Get(server.URL + "/internal/model-gateway/stats")
	require.NoError(t, err)
	defer resp.Body.Close()
	require.Equal(t, http.StatusUnauthorized, resp.StatusCode)
}

func TestModelGatewayStatsEndpointReturnsKindStats(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer upstream.Close()
	server := newGatewayTestServer(t, testGatewayConfig(upstream.URL), zap.NewNop())
	defer server.Close()

	req, err := http.NewRequest(http.MethodGet, server.URL+"/internal/model-gateway/stats", nil)
	require.NoError(t, err)
	req.Header.Set(HeaderInternalToken, "test-token")
	resp, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	defer resp.Body.Close()
	require.Equal(t, http.StatusOK, resp.StatusCode)
	var stats Stats
	require.NoError(t, json.NewDecoder(resp.Body).Decode(&stats))
	require.True(t, stats[KindChat].Enabled)
	require.True(t, stats[KindEmbedding].Enabled)
	require.True(t, stats[KindRerank].Enabled)
}

func assertKindPassthrough(t *testing.T, kind string, path string, responseBody string) {
	t.Helper()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		require.JSONEq(t, `{"input":"demo"}`, string(body))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(responseBody))
	}))
	defer upstream.Close()
	cfg := testGatewayConfig(upstream.URL)
	server := newGatewayTestServer(t, cfg, zap.NewNop())
	defer server.Close()
	req := newGatewayRequest(t, server.URL+path, "test-token", `{"input":"demo"}`)
	req.Header.Set(HeaderModelKind, kind)
	resp, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	defer resp.Body.Close()
	got, _ := io.ReadAll(resp.Body)
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.JSONEq(t, responseBody, string(got))
}

func sendMarkedChat(t *testing.T, baseURL string, marker string, fieldID string) resultForMarker {
	t.Helper()
	req := newGatewayRequest(t, baseURL+"/internal/model-gateway/v1/chat/completions", "test-token", `{"question":"`+marker+`"}`)
	req.Header.Set(HeaderRunID, "run-1")
	req.Header.Set(HeaderFieldID, fieldID)
	resp, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	return resultForMarker{marker: marker, body: string(body), requestID: resp.Header.Get(HeaderRequestID)}
}

type resultForMarker struct {
	marker    string
	body      string
	requestID string
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func newGatewayRequest(t *testing.T, url string, token string, body string) *http.Request {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewBufferString(body))
	require.NoError(t, err)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(HeaderRunID, "run-1")
	req.Header.Set(HeaderModelKind, KindChat)
	if token != "" {
		req.Header.Set(HeaderInternalToken, token)
	}
	return req
}

func postGateway(t *testing.T, url string, token string, body string) *http.Response {
	t.Helper()
	req := newGatewayRequest(t, url, token, body)
	resp, err := http.DefaultClient.Do(req)
	require.NoError(t, err)
	return resp
}

func newGatewayTestServer(t *testing.T, cfg config.ModelGatewayConfig, logger *zap.Logger) *httptest.Server {
	t.Helper()
	service := newGatewayTestService(t, cfg, logger)
	r := chi.NewRouter()
	NewHandler(service).RegisterRoutes(r)
	return httptest.NewServer(r)
}

func newGatewayTestService(t *testing.T, cfg config.ModelGatewayConfig, logger *zap.Logger) *Service {
	t.Helper()
	t.Setenv("NDR_MODEL_GATEWAY_TOKEN", "test-token")
	service, err := NewService(cfg, logger)
	require.NoError(t, err)
	return service
}

func testGatewayConfig(upstreamURL string) config.ModelGatewayConfig {
	cfg := config.Default().ModelGateway
	cfg.Enabled = true
	cfg.BindToAPI = true
	cfg.InternalTokenEnv = "NDR_MODEL_GATEWAY_TOKEN"
	cfg.Defaults.RequestTimeoutSeconds = 2
	cfg.Defaults.QueueTimeoutSeconds = 2
	cfg.Defaults.RetryMaxAttempts = 1
	cfg.Defaults.RetryBaseDelayMillis = 1
	cfg.Defaults.RetryMaxDelayMillis = 5
	cfg.Defaults.CircuitFailureThreshold = 5
	cfg.Defaults.CircuitOpenSeconds = 1
	cfg.Chat.UpstreamURL = upstreamURL
	cfg.Chat.QPS = 0
	cfg.Chat.RPM = 0
	cfg.Chat.MaxConcurrency = 2
	cfg.Chat.MaxQueueSize = 10
	cfg.Chat.PerRunMaxInflight = 2
	cfg.Chat.TimeoutSeconds = 2
	cfg.Embedding.UpstreamURL = upstreamURL
	cfg.Embedding.QPS = 0
	cfg.Embedding.RPM = 0
	cfg.Embedding.MaxConcurrency = 2
	cfg.Embedding.MaxQueueSize = 10
	cfg.Embedding.TimeoutSeconds = 2
	cfg.Rerank.UpstreamURL = upstreamURL
	cfg.Rerank.QPS = 0
	cfg.Rerank.RPM = 0
	cfg.Rerank.MaxConcurrency = 2
	cfg.Rerank.MaxQueueSize = 10
	cfg.Rerank.TimeoutSeconds = 2
	return cfg
}
