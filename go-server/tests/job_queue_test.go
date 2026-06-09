package tests

import (
	"encoding/json"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestTaskPayloadEncodesOnlyJobID(t *testing.T) {
	jobID := uuid.New()

	payload, err := jobs.EncodeTaskPayload(jobID)

	require.NoError(t, err)
	var decoded map[string]any
	require.NoError(t, json.Unmarshal(payload, &decoded))
	require.Equal(t, map[string]any{"job_id": jobID.String()}, decoded)
	taskPayload, err := jobs.DecodeTaskPayload(payload)
	require.NoError(t, err)
	require.Equal(t, jobID, taskPayload.JobID)
}

func TestTaskTypeUsesNamespace(t *testing.T) {
	require.Equal(t, "gongkan:noop", jobs.TaskType("", jobs.JobTypeNoop))
	require.Equal(t, "custom:fill_form", jobs.TaskType("custom", jobs.JobTypeFillForm))
}
