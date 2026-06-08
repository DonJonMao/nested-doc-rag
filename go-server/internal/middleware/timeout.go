package middleware

import (
	"bytes"
	"context"
	"net/http"
	"sync"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
)

func Timeout(timeout time.Duration) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if timeout <= 0 {
				next.ServeHTTP(w, r)
				return
			}
			ctx, cancel := context.WithTimeout(r.Context(), timeout)
			defer cancel()
			tw := newTimeoutWriter(w)
			done := make(chan struct{})
			go func() {
				defer close(done)
				next.ServeHTTP(tw, r.WithContext(ctx))
			}()
			select {
			case <-done:
				tw.flush()
			case <-ctx.Done():
				tw.markTimedOut()
				httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeInternal, "request timed out", http.StatusGatewayTimeout, nil, ctx.Err()))
			}
		})
	}
}

type timeoutWriter struct {
	w        http.ResponseWriter
	mu       sync.Mutex
	header   http.Header
	body     bytes.Buffer
	status   int
	timedOut bool
}

func newTimeoutWriter(w http.ResponseWriter) *timeoutWriter {
	return &timeoutWriter{w: w, header: make(http.Header), status: http.StatusOK}
}

func (tw *timeoutWriter) Header() http.Header {
	return tw.header
}

func (tw *timeoutWriter) Write(data []byte) (int, error) {
	tw.mu.Lock()
	defer tw.mu.Unlock()
	if tw.timedOut {
		return 0, context.DeadlineExceeded
	}
	return tw.body.Write(data)
}

func (tw *timeoutWriter) WriteHeader(status int) {
	tw.mu.Lock()
	defer tw.mu.Unlock()
	tw.status = status
}

func (tw *timeoutWriter) markTimedOut() {
	tw.mu.Lock()
	defer tw.mu.Unlock()
	tw.timedOut = true
}

func (tw *timeoutWriter) flush() {
	tw.mu.Lock()
	defer tw.mu.Unlock()
	if tw.timedOut {
		return
	}
	for key, values := range tw.header {
		for _, value := range values {
			tw.w.Header().Add(key, value)
		}
	}
	tw.w.WriteHeader(tw.status)
	_, _ = tw.w.Write(tw.body.Bytes())
}
