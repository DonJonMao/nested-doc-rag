package jobs

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// CreateJobTx has no queue, event or audit side effects before caller commit.
func (s *Service) CreateJobTx(ctx context.Context, tx pgx.Tx, req CreateJobRequest, actor auth.Principal) (*Job, error) {
	if tx == nil || !ValidJobType(req.JobType) || !ValidResourceType(req.ResourceType) {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid transactional job request", http.StatusBadRequest, nil, nil)
	}
	if requiresWorkspaceWrite(req.JobType, req.ResourceType) {
		if err := s.authorizer.CanWriteWorkspace(ctx, req.WorkspaceID, actor); err != nil {
			return nil, err
		}
	}
	id, now := uuid.New(), time.Now().UTC()
	resourceID := req.ResourceID
	if resourceID == uuid.Nil {
		resourceID = id
	}
	attempts := req.MaxAttempts
	if attempts <= 0 {
		attempts = s.maxAttempts
	}
	job := &Job{ID: id, WorkspaceID: req.WorkspaceID, JobType: req.JobType,
		ResourceType: req.ResourceType, ResourceID: resourceID, Status: JobStatusCreated,
		Priority: req.Priority, MaxAttempts: attempts, Payload: req.Payload,
		CreatedBy: actor.UserID, CreatedAt: now, UpdatedAt: now}
	if job.Payload == nil {
		job.Payload = map[string]any{}
	}
	if err := insertJob(ctx, tx, *job); err != nil {
		return nil, err
	}
	return job, nil
}

// EnqueuePersistedJob re-reads a committed row, never a caller-supplied payload.
// Queue failure leaves a queued row for periodic recovery; domain pins survive.
func (s *Service) EnqueuePersistedJob(ctx context.Context, persisted *Job) error {
	if persisted == nil {
		return errors.New("persisted job is required")
	}
	job, err := s.repo.GetByID(ctx, persisted.ID)
	if err != nil {
		return err
	}
	if job.Status == JobStatusCreated {
		now := time.Now().UTC()
		if err := s.repo.MarkQueued(ctx, job.ID, now); err != nil {
			return err
		}
		job.Status, job.QueuedAt = JobStatusQueued, &now
		s.emit(ctx, *job, runevent.EventQueued, map[string]any{"post_commit": true})
		if s.metrics != nil {
			s.metrics.ObserveJobQueued(job.JobType)
		}
	} else if job.Status != JobStatusQueued {
		return nil
	}
	if s.queue == nil {
		return nil
	}
	return s.queue.Enqueue(ctx, *job)
}

type pendingDispatchLister interface {
	ListPendingDispatch(context.Context, int) ([]Job, error)
}

// NotifyCommittedCancellation emits the result of a domain cancellation Tx;
// it must never mutate a job which that transaction has already canceled.
func (s *Service) NotifyCommittedCancellation(ctx context.Context, id uuid.UUID) error {
	job, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return err
	}
	switch job.Status {
	case JobStatusCanceled:
		s.emit(ctx, *job, runevent.EventCanceled, map[string]any{"post_commit": true})
	case JobStatusCancelRequested:
		s.emit(ctx, *job, runevent.EventCancelRequested, map[string]any{"post_commit": true})
	default:
		return errors.New("job has no committed cancellation to notify")
	}
	return nil
}

type pendingDispatchPager interface {
	ListPendingDispatchPage(context.Context, *time.Time, uuid.UUID, time.Time, int) ([]Job, error)
}

type publishedStateWriter interface {
	MarkPublishedSucceeded(context.Context, uuid.UUID, time.Time) error
}

func (s *Service) completePublishedJob(ctx context.Context, job Job) error {
	writer, ok := s.repo.(publishedStateWriter)
	if !ok {
		return s.MarkSucceeded(ctx, job)
	}
	now := time.Now().UTC()
	if err := writer.MarkPublishedSucceeded(ctx, job.ID, now); err != nil {
		return err
	}
	job.Status, job.FinishedAt = JobStatusSucceeded, &now
	s.emit(ctx, job, runevent.EventSucceeded, map[string]any{"publication_recovered": true})
	return nil
}

// Domain publication is the durable proof. Repair can finish a failed/queued
// job without starting Python again or consuming another attempt.
func (r *PGXRepo) MarkPublishedSucceeded(ctx context.Context, id uuid.UUID, finishedAt time.Time) error {
	tag, err := r.pool.Exec(ctx, `UPDATE jobs j SET status='succeeded',finished_at=$2,error_message=NULL,updated_at=$2
		FROM ingestion_jobs i JOIN knowledge_index_versions v ON v.id=i.index_version_id AND v.knowledge_base_id=i.knowledge_base_id AND v.workspace_id=i.workspace_id
		WHERE j.id=$1 AND i.job_id=j.id AND i.workspace_id=j.workspace_id AND i.status='succeeded'
		AND v.validation_state='validated' AND v.publication_state IN ('activated','superseded')
		AND j.status IN ('created','queued','running','failed','completed_with_failures','cancel_requested','canceled','succeeded')`, id, finishedAt)
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return errors.New("job has no committed index publication to recover")
	}
	return nil
}

func (s *Service) RecoverPendingDispatch(ctx context.Context) (int, error) {
	lister, ok := s.repo.(pendingDispatchLister)
	if !ok {
		return 0, nil
	}
	count := 0
	var failures []error
	pager, paged := s.repo.(pendingDispatchPager)
	cutoff := time.Now().UTC()
	var after *time.Time
	var afterID uuid.UUID
	for {
		var rows []Job
		var err error
		if paged {
			rows, err = pager.ListPendingDispatchPage(ctx, after, afterID, cutoff, 500)
		} else {
			rows, err = lister.ListPendingDispatch(ctx, 500)
		}
		if err != nil {
			failures = append(failures, err)
			break
		}
		for i := range rows {
			if err := s.EnqueuePersistedJob(ctx, &rows[i]); err != nil {
				failures = append(failures, err)
			} else {
				count++
			}
		}
		if !paged || len(rows) < 500 {
			break
		}
		last := rows[len(rows)-1]
		after, afterID = &last.CreatedAt, last.ID
	}
	return count, errors.Join(failures...)
}

func (r *PGXRepo) ListPendingDispatch(ctx context.Context, limit int) ([]Job, error) {
	return r.ListPendingDispatchPage(ctx, nil, uuid.Nil, time.Now().UTC(), limit)
}

func (r *PGXRepo) ListPendingDispatchPage(ctx context.Context, after *time.Time, afterID uuid.UUID, cutoff time.Time, limit int) ([]Job, error) {
	rows, err := r.pool.Query(ctx, `SELECT id, workspace_id, job_type, resource_type, resource_id, status, priority, attempt,
		max_attempts, payload_json, COALESCE(error_message, ''), cancel_requested_at,
		queued_at, started_at, heartbeat_at, finished_at, created_by, created_at, updated_at
		FROM jobs WHERE status IN ('created','queued') AND created_at <= $3
		AND ($1::timestamptz IS NULL OR (created_at,id) > ($1,$2::uuid))
		ORDER BY created_at,id LIMIT $4`, after, afterID, cutoff, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var pending []Job
	for rows.Next() {
		job, err := scanJob(rows)
		if err != nil {
			return nil, err
		}
		pending = append(pending, *job)
	}
	return pending, rows.Err()
}
