package jobs

import (
	"context"
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
)

type ResourceLimiter struct {
	PythonProcesses chan struct{}
	FillRuns        chan struct{}
	IngestionRuns   chan struct{}
}

func NewResourceLimiter(cfg config.JobsConfig) *ResourceLimiter {
	return &ResourceLimiter{
		PythonProcesses: make(chan struct{}, positiveOrOne(cfg.MaxPythonProcesses)),
		FillRuns:        make(chan struct{}, positiveOrOne(cfg.FillConcurrency)),
		IngestionRuns:   make(chan struct{}, positiveOrOne(cfg.IngestionConcurrency)),
	}
}

func (l *ResourceLimiter) Acquire(ctx context.Context, jobType string) (func(), error) {
	if l == nil {
		return func() {}, nil
	}
	var acquired []chan struct{}
	acquire := func(ch chan struct{}) error {
		if ch == nil {
			return nil
		}
		select {
		case ch <- struct{}{}:
			acquired = append(acquired, ch)
			return nil
		case <-ctx.Done():
			for i := len(acquired) - 1; i >= 0; i-- {
				<-acquired[i]
			}
			return httpx.NewAppError(httpx.CodeInternal, "job resource acquisition canceled", http.StatusInternalServerError, nil, ctx.Err())
		}
	}
	switch jobType {
	case JobTypeFillForm:
		if err := acquire(l.FillRuns); err != nil {
			return nil, err
		}
		if err := acquire(l.PythonProcesses); err != nil {
			return nil, err
		}
	case JobTypeIngestKnowledge:
		if err := acquire(l.IngestionRuns); err != nil {
			return nil, err
		}
		if err := acquire(l.PythonProcesses); err != nil {
			return nil, err
		}
	}
	return func() {
		for i := len(acquired) - 1; i >= 0; i-- {
			<-acquired[i]
		}
	}, nil
}

func positiveOrOne(value int) int {
	if value <= 0 {
		return 1
	}
	return value
}
