package middleware

import (
	"net"
	"net/http"
	"sync"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
)

type ipBucket struct {
	tokens     float64
	lastRefill time.Time
	lastSeen   time.Time
}

type IPRateLimiter struct {
	mu      sync.Mutex
	rps     float64
	burst   float64
	buckets map[string]*ipBucket
	now     func() time.Time
}

func NewIPRateLimiter(rps int, burst int) *IPRateLimiter {
	if rps <= 0 {
		rps = 20
	}
	if burst <= 0 {
		burst = 40
	}
	return &IPRateLimiter{
		rps:     float64(rps),
		burst:   float64(burst),
		buckets: make(map[string]*ipBucket),
		now:     time.Now,
	}
}

func (l *IPRateLimiter) Allow(ip string) bool {
	if l == nil {
		return true
	}
	if ip == "" {
		ip = "unknown"
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	bucket := l.buckets[ip]
	if bucket == nil {
		bucket = &ipBucket{tokens: l.burst, lastRefill: now, lastSeen: now}
		l.buckets[ip] = bucket
	}
	elapsed := now.Sub(bucket.lastRefill).Seconds()
	if elapsed > 0 {
		bucket.tokens += elapsed * l.rps
		if bucket.tokens > l.burst {
			bucket.tokens = l.burst
		}
		bucket.lastRefill = now
	}
	bucket.lastSeen = now
	if bucket.tokens < 1 {
		l.cleanupLocked(now)
		return false
	}
	bucket.tokens--
	l.cleanupLocked(now)
	return true
}

func (l *IPRateLimiter) cleanupLocked(now time.Time) {
	for ip, bucket := range l.buckets {
		if now.Sub(bucket.lastSeen) > 10*time.Minute {
			delete(l.buckets, ip)
		}
	}
}

func RateLimit(cfg config.SecurityConfig) func(http.Handler) http.Handler {
	if !cfg.RateLimitEnabled {
		return func(next http.Handler) http.Handler { return next }
	}
	limiter := NewIPRateLimiter(cfg.RateLimitRPS, cfg.RateLimitBurst)
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if !limiter.Allow(clientIP(r)) {
				httpx.WriteError(w, r, httpx.NewAppError(httpx.CodeRateLimited, "rate limit exceeded", http.StatusTooManyRequests, nil, nil))
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err == nil && host != "" {
		return host
	}
	if r.RemoteAddr != "" {
		return r.RemoteAddr
	}
	return "unknown"
}
