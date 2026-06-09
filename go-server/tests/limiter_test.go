package tests

import (
	"context"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/stretchr/testify/require"
)

func TestResourceLimiterAcquireReleaseFillAndIngestion(t *testing.T) {
	limiter := jobs.NewResourceLimiter(config.JobsConfig{FillConcurrency: 1, IngestionConcurrency: 1, MaxPythonProcesses: 2})

	releaseFill, err := limiter.Acquire(context.Background(), jobs.JobTypeFillForm)
	require.NoError(t, err)
	require.Len(t, limiter.FillRuns, 1)
	require.Len(t, limiter.PythonProcesses, 1)
	releaseFill()
	require.Empty(t, limiter.FillRuns)
	require.Empty(t, limiter.PythonProcesses)

	releaseIngest, err := limiter.Acquire(context.Background(), jobs.JobTypeIngestKnowledge)
	require.NoError(t, err)
	require.Len(t, limiter.IngestionRuns, 1)
	require.Len(t, limiter.PythonProcesses, 1)
	releaseIngest()
}

func TestResourceLimiterContextCancel(t *testing.T) {
	limiter := jobs.NewResourceLimiter(config.JobsConfig{FillConcurrency: 1, IngestionConcurrency: 1, MaxPythonProcesses: 1})
	release, err := limiter.Acquire(context.Background(), jobs.JobTypeFillForm)
	require.NoError(t, err)
	defer release()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()

	_, err = limiter.Acquire(ctx, jobs.JobTypeFillForm)

	require.Error(t, err)
}
