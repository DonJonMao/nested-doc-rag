package modelgateway

import (
	"context"
	"sync"
	"time"
)

type rateLimiter struct {
	mu        sync.Mutex
	qps       int
	rpm       int
	next      time.Time
	rpmEvents []time.Time
}

func newRateLimiter(qps int, rpm int) *rateLimiter {
	return &rateLimiter{qps: qps, rpm: rpm}
}

func (l *rateLimiter) wait(ctx context.Context) error {
	if l == nil || (l.qps <= 0 && l.rpm <= 0) {
		return nil
	}
	for {
		wait := l.reserveDelay()
		if wait <= 0 {
			return nil
		}
		timer := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}

func (l *rateLimiter) reserveDelay() time.Duration {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := time.Now()
	var wait time.Duration
	if l.qps > 0 && now.Before(l.next) {
		wait = l.next.Sub(now)
	}
	if l.rpm > 0 {
		cutoff := now.Add(-time.Minute)
		kept := l.rpmEvents[:0]
		for _, event := range l.rpmEvents {
			if event.After(cutoff) {
				kept = append(kept, event)
			}
		}
		l.rpmEvents = kept
		if len(l.rpmEvents) >= l.rpm {
			rpmWait := l.rpmEvents[0].Add(time.Minute).Sub(now)
			if rpmWait > wait {
				wait = rpmWait
			}
		}
	}
	if wait > 0 {
		return wait
	}
	if l.qps > 0 {
		interval := time.Second / time.Duration(l.qps)
		if interval <= 0 {
			interval = time.Second
		}
		l.next = now.Add(interval)
	}
	if l.rpm > 0 {
		l.rpmEvents = append(l.rpmEvents, now)
	}
	return 0
}
