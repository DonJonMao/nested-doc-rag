package modelgateway

import (
	"sync"
	"time"
)

const (
	circuitClosed   = "closed"
	circuitOpen     = "open"
	circuitHalfOpen = "half_open"
)

type circuitBreaker struct {
	mu          sync.Mutex
	threshold   int
	openFor     time.Duration
	failures    int
	state       string
	openedAt    time.Time
	probeActive bool
}

func newCircuitBreaker(threshold int, openFor time.Duration) *circuitBreaker {
	if threshold <= 0 {
		threshold = 5
	}
	if openFor <= 0 {
		openFor = 30 * time.Second
	}
	return &circuitBreaker{threshold: threshold, openFor: openFor, state: circuitClosed}
}

func (b *circuitBreaker) allow() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	now := time.Now()
	switch b.state {
	case circuitOpen:
		if now.Sub(b.openedAt) < b.openFor {
			return false
		}
		b.state = circuitHalfOpen
		b.probeActive = false
		fallthrough
	case circuitHalfOpen:
		if b.probeActive {
			return false
		}
		b.probeActive = true
		return true
	default:
		return true
	}
}

func (b *circuitBreaker) recordSuccess() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.state = circuitClosed
	b.failures = 0
	b.probeActive = false
}

func (b *circuitBreaker) recordFailure() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.probeActive = false
	if b.state == circuitHalfOpen {
		b.openLocked()
		return
	}
	b.failures++
	if b.failures >= b.threshold {
		b.openLocked()
	}
}

func (b *circuitBreaker) openLocked() {
	b.state = circuitOpen
	b.openedAt = time.Now()
	b.failures = 0
}

func (b *circuitBreaker) stateName() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.state == circuitOpen && time.Since(b.openedAt) >= b.openFor {
		return circuitHalfOpen
	}
	return b.state
}
