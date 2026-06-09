package python

import (
	"context"
	"sync"
	"time"
)

type FakeRunner struct {
	mu sync.Mutex

	Step15Result   *Step15RunResult
	IngestResult   *IngestionResult
	ValidateResult *ArtifactValidationResult
	Err            error
	Step15Err      error
	IngestErr      error
	ValidateErr    error
	Delay          time.Duration

	Step15Calls   []Step15RunRequest
	IngestCalls   []IngestionRequest
	ValidateCalls []string
}

func (r *FakeRunner) RunStep15Agent(ctx context.Context, req Step15RunRequest) (*Step15RunResult, error) {
	if err := r.wait(ctx); err != nil {
		return nil, err
	}
	r.mu.Lock()
	r.Step15Calls = append(r.Step15Calls, req)
	result := r.Step15Result
	err := r.err(r.Step15Err)
	r.mu.Unlock()
	if err != nil {
		return result, err
	}
	if result != nil {
		return result, nil
	}
	return &Step15RunResult{RunID: req.RunID, OutDir: req.OutDir, ExitCode: 0, StartedAt: time.Now().UTC(), FinishedAt: time.Now().UTC()}, nil
}

func (r *FakeRunner) RunKnowledgeIngestion(ctx context.Context, req IngestionRequest) (*IngestionResult, error) {
	if err := r.wait(ctx); err != nil {
		return nil, err
	}
	r.mu.Lock()
	r.IngestCalls = append(r.IngestCalls, req)
	result := r.IngestResult
	err := r.err(r.IngestErr)
	r.mu.Unlock()
	if err != nil {
		return result, err
	}
	if result != nil {
		return result, nil
	}
	return &IngestionResult{IngestionID: req.IngestionID, OutDir: req.OutDir, ExitCode: 0, StartedAt: time.Now().UTC(), FinishedAt: time.Now().UTC()}, nil
}

func (r *FakeRunner) ValidateArtifacts(ctx context.Context, runDir string) (*ArtifactValidationResult, error) {
	if err := r.wait(ctx); err != nil {
		return nil, err
	}
	r.mu.Lock()
	r.ValidateCalls = append(r.ValidateCalls, runDir)
	result := r.ValidateResult
	err := r.err(r.ValidateErr)
	r.mu.Unlock()
	if err != nil {
		return result, err
	}
	if result != nil {
		return result, nil
	}
	return &ArtifactValidationResult{RunDir: runDir, OK: true}, nil
}

func (r *FakeRunner) wait(ctx context.Context) error {
	if r == nil || r.Delay <= 0 {
		return nil
	}
	timer := time.NewTimer(r.Delay)
	defer timer.Stop()
	select {
	case <-timer.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (r *FakeRunner) err(specific error) error {
	if specific != nil {
		return specific
	}
	return r.Err
}
