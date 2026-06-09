package jobs

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
)

type TaskHandler interface {
	Handle(ctx context.Context, job *Job) error
}

type NoopHandler struct {
	events RunEventWriter
}

func NewNoopHandler(events RunEventWriter) *NoopHandler {
	return &NoopHandler{events: events}
}

func (h *NoopHandler) Handle(ctx context.Context, job *Job) error {
	if job == nil {
		return errors.New("job is nil")
	}
	h.emitProgress(ctx, job, 0, "noop started")
	sleepMS := intFromPayload(job.Payload, "sleep_ms")
	if sleepMS > 0 {
		timer := time.NewTimer(time.Duration(sleepMS) * time.Millisecond)
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-ctx.Done():
			return ErrJobCanceled
		}
	}
	select {
	case <-ctx.Done():
		return ErrJobCanceled
	default:
	}
	h.emitProgress(ctx, job, 100, "noop completed")
	return nil
}

func (h *NoopHandler) emitProgress(ctx context.Context, job *Job, percent int, message string) {
	if h == nil || h.events == nil || job == nil {
		return
	}
	jobID := job.ID
	_, _ = h.events.Create(ctx, runevent.RunEvent{
		WorkspaceID: job.WorkspaceID,
		RunID:       job.ResourceID,
		JobID:       &jobID,
		EventType:   runevent.EventProgress,
		Payload: map[string]any{
			"job_id":   job.ID.String(),
			"job_type": job.JobType,
			"percent":  percent,
			"message":  message,
		},
	})
}

type PlaceholderHandler struct {
	jobType string
}

func NewPlaceholderHandler(jobType string) *PlaceholderHandler {
	return &PlaceholderHandler{jobType: jobType}
}

func (h *PlaceholderHandler) Handle(ctx context.Context, job *Job) error {
	_ = ctx
	jobType := h.jobType
	if job != nil && job.JobType != "" {
		jobType = job.JobType
	}
	return fmt.Errorf("%w: %s handler not implemented in Block 3", ErrHandlerNotImplemented, jobType)
}

func intFromPayload(payload map[string]any, key string) int {
	switch value := payload[key].(type) {
	case int:
		return value
	case int64:
		return int(value)
	case float64:
		return int(value)
	case jsonNumber:
		parsed, _ := value.Int64()
		return int(parsed)
	default:
		return 0
	}
}

type jsonNumber interface {
	Int64() (int64, error)
}
