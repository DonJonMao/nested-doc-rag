package jobs

import (
	"context"
	"encoding/json"

	"github.com/google/uuid"
)

type Queue interface {
	Enqueue(ctx context.Context, job Job) error
	Close() error
}

type TaskPayload struct {
	JobID uuid.UUID `json:"job_id"`
}

func EncodeTaskPayload(jobID uuid.UUID) ([]byte, error) {
	return json.Marshal(TaskPayload{JobID: jobID})
}

func DecodeTaskPayload(data []byte) (TaskPayload, error) {
	var payload TaskPayload
	err := json.Unmarshal(data, &payload)
	return payload, err
}
