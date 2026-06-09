package tests

import (
	"net/http"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/stretchr/testify/require"
)

func TestJobStateMachineLegalTransitions(t *testing.T) {
	legal := [][2]string{
		{jobs.JobStatusCreated, jobs.JobStatusQueued},
		{jobs.JobStatusQueued, jobs.JobStatusRunning},
		{jobs.JobStatusQueued, jobs.JobStatusCanceled},
		{jobs.JobStatusRunning, jobs.JobStatusSucceeded},
		{jobs.JobStatusRunning, jobs.JobStatusCompletedWithFailures},
		{jobs.JobStatusRunning, jobs.JobStatusFailed},
		{jobs.JobStatusRunning, jobs.JobStatusCancelRequested},
		{jobs.JobStatusRunning, jobs.JobStatusCanceled},
		{jobs.JobStatusCancelRequested, jobs.JobStatusCanceled},
		{jobs.JobStatusCancelRequested, jobs.JobStatusFailed},
		{jobs.JobStatusFailed, jobs.JobStatusQueued},
		{jobs.JobStatusCompletedWithFailures, jobs.JobStatusQueued},
	}
	for _, item := range legal {
		require.True(t, jobs.CanTransition(item[0], item[1]), "%s -> %s", item[0], item[1])
		require.NoError(t, jobs.ValidateTransition(item[0], item[1]))
	}
}

func TestJobStateMachineIllegalTransitions(t *testing.T) {
	illegal := [][2]string{
		{jobs.JobStatusSucceeded, jobs.JobStatusRunning},
		{jobs.JobStatusCanceled, jobs.JobStatusRunning},
		{jobs.JobStatusSucceeded, jobs.JobStatusFailed},
		{jobs.JobStatusCreated, jobs.JobStatusRunning},
	}
	for _, item := range illegal {
		require.False(t, jobs.CanTransition(item[0], item[1]), "%s -> %s", item[0], item[1])
		err := jobs.ValidateTransition(item[0], item[1])
		requireAppError(t, err, httpx.CodeConflict, http.StatusConflict)
	}
}
