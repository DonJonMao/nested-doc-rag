package sse

import (
	"time"

	"github.com/google/uuid"
)

type Event struct {
	RunID     uuid.UUID      `json:"run_id"`
	EventType string         `json:"event_type"`
	Sequence  int64          `json:"sequence"`
	Payload   map[string]any `json:"payload"`
	CreatedAt time.Time      `json:"created_at"`
}
