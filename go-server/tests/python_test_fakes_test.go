package tests

import (
	"context"
	"sync"
	"time"

	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
)

type fakeCommandExecutor struct {
	mu      sync.Mutex
	results []*pythonpkg.ProcessResult
	errs    []error
	specs   []pythonpkg.CommandSpec
}

func (f *fakeCommandExecutor) Run(ctx context.Context, spec pythonpkg.CommandSpec, timeout time.Duration) (*pythonpkg.ProcessResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.specs = append(f.specs, spec)
	var result *pythonpkg.ProcessResult
	if len(f.results) > 0 {
		result = f.results[0]
		f.results = f.results[1:]
	} else {
		now := time.Now().UTC()
		result = &pythonpkg.ProcessResult{ExitCode: 0, StartedAt: now, FinishedAt: now}
	}
	var err error
	if len(f.errs) > 0 {
		err = f.errs[0]
		f.errs = f.errs[1:]
	}
	return result, err
}
