package jobs

import (
	"context"
	"net/http"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
)

type publicationAwareCanceler interface {
	CancelUnpublishedJob(context.Context, uuid.UUID, time.Time) (*Job, error)
}

type IngestionJobCanceler interface {
	// A nil result means there is no domain ingestion associated with this job.
	CancelWorkerJob(context.Context, uuid.UUID, uuid.UUID) (*Job, error)
}

func (s *Service) SetIngestionJobCanceler(canceler IngestionJobCanceler) {
	s.ingestionCanceler = canceler
}

// Lock the job before checking publication. CompleteBuild uses the same job
// lock: cancellation first prevents its commit; publication first is immutable.
func (r *PGXRepo) CancelUnpublishedJob(ctx context.Context, id uuid.UUID, now time.Time) (*Job, error) {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	job, err := scanJob(tx.QueryRow(ctx, `SELECT id, workspace_id, job_type, resource_type, resource_id, status, priority, attempt,
		max_attempts, payload_json, COALESCE(error_message, ''), cancel_requested_at,
		queued_at, started_at, heartbeat_at, finished_at, created_by, created_at, updated_at
		FROM jobs WHERE id=$1 FOR UPDATE`, id))
	if err != nil {
		return nil, err
	}
	var published bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM ingestion_jobs i JOIN knowledge_index_versions v
		ON v.id=i.index_version_id AND v.knowledge_base_id=i.knowledge_base_id AND v.workspace_id=i.workspace_id
		WHERE i.job_id=$1 AND i.status='succeeded' AND v.validation_state='validated' AND v.publication_state IN ('activated','superseded'))`, id).Scan(&published); err != nil {
		return nil, err
	}
	if published {
		return nil, httpx.NewAppError(httpx.CodeConflict, "published index build cannot be canceled", http.StatusConflict, nil, nil)
	}
	switch job.Status {
	case JobStatusCreated, JobStatusQueued:
		job.Status, job.FinishedAt = JobStatusCanceled, &now
	case JobStatusRunning:
		job.Status, job.CancelRequestedAt = JobStatusCancelRequested, &now
	case JobStatusCancelRequested:
	default:
		return nil, httpx.NewAppError(httpx.CodeConflict, "job cannot be canceled from current status", http.StatusConflict, map[string]string{"status": job.Status}, nil)
	}
	if _, err := tx.Exec(ctx, `UPDATE jobs SET status=$2,cancel_requested_at=$3,finished_at=$4,updated_at=$5 WHERE id=$1`, id, job.Status, job.CancelRequestedAt, job.FinishedAt, now); err != nil {
		return nil, err
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return job, nil
}
