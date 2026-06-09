package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Repo interface {
	Create(ctx context.Context, job Job) error
	GetByID(ctx context.Context, id uuid.UUID) (*Job, error)
	ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int) ([]Job, error)
	UpdateStatus(ctx context.Context, id uuid.UUID, fromStatus string, toStatus string, fields UpdateStatusFields) error
	MarkQueued(ctx context.Context, id uuid.UUID, queuedAt time.Time) error
	MarkRunning(ctx context.Context, id uuid.UUID, startedAt time.Time) error
	MarkHeartbeat(ctx context.Context, id uuid.UUID, heartbeatAt time.Time) error
	MarkSucceeded(ctx context.Context, id uuid.UUID, finishedAt time.Time) error
	MarkCompletedWithFailures(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error
	MarkFailed(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error
	MarkEnqueueFailed(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error
	RequestCancel(ctx context.Context, id uuid.UUID, t time.Time) error
	MarkCanceled(ctx context.Context, id uuid.UUID, finishedAt time.Time) error
	IncrementAttempt(ctx context.Context, id uuid.UUID) error
}

type PGXRepo struct {
	pool *pgxpool.Pool
}

func NewPGXRepo(pool *pgxpool.Pool) *PGXRepo {
	return &PGXRepo{pool: pool}
}

func (r *PGXRepo) Create(ctx context.Context, job Job) error {
	if job.ID == uuid.Nil {
		job.ID = uuid.New()
	}
	now := time.Now().UTC()
	if job.CreatedAt.IsZero() {
		job.CreatedAt = now
	}
	if job.UpdatedAt.IsZero() {
		job.UpdatedAt = now
	}
	payload, err := json.Marshal(job.Payload)
	if err != nil {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "invalid job payload", http.StatusBadRequest, nil, err)
	}
	_, err = r.pool.Exec(ctx, `
		INSERT INTO jobs (
			id, workspace_id, job_type, resource_type, resource_id, status, priority,
			attempt, max_attempts, payload_json, error_message, cancel_requested_at,
			queued_at, started_at, heartbeat_at, finished_at, created_by, created_at, updated_at
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
	`, job.ID, job.WorkspaceID, job.JobType, job.ResourceType, job.ResourceID, job.Status, job.Priority, job.Attempt, job.MaxAttempts, payload, job.ErrorMessage, job.CancelRequestedAt, job.QueuedAt, job.StartedAt, job.HeartbeatAt, job.FinishedAt, job.CreatedBy, job.CreatedAt, job.UpdatedAt)
	return mapDBError(err, "job already exists", "job not found")
}

func (r *PGXRepo) GetByID(ctx context.Context, id uuid.UUID) (*Job, error) {
	return scanJob(r.pool.QueryRow(ctx, `
		SELECT id, workspace_id, job_type, resource_type, resource_id, status, priority, attempt,
			max_attempts, payload_json, COALESCE(error_message, ''), cancel_requested_at,
			queued_at, started_at, heartbeat_at, finished_at, created_by, created_at, updated_at
		FROM jobs WHERE id = $1
	`, id))
}

func (r *PGXRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int) ([]Job, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	var rows pgx.Rows
	var err error
	if status != "" {
		rows, err = r.pool.Query(ctx, `
			SELECT id, workspace_id, job_type, resource_type, resource_id, status, priority, attempt,
				max_attempts, payload_json, COALESCE(error_message, ''), cancel_requested_at,
				queued_at, started_at, heartbeat_at, finished_at, created_by, created_at, updated_at
			FROM jobs WHERE workspace_id = $1 AND status = $2
			ORDER BY created_at DESC LIMIT $3 OFFSET $4
		`, workspaceID, status, limit, offset)
	} else {
		rows, err = r.pool.Query(ctx, `
			SELECT id, workspace_id, job_type, resource_type, resource_id, status, priority, attempt,
				max_attempts, payload_json, COALESCE(error_message, ''), cancel_requested_at,
				queued_at, started_at, heartbeat_at, finished_at, created_by, created_at, updated_at
			FROM jobs WHERE workspace_id = $1
			ORDER BY created_at DESC LIMIT $2 OFFSET $3
		`, workspaceID, limit, offset)
	}
	if err != nil {
		return nil, mapDBError(err, "list jobs conflict", "jobs not found")
	}
	defer rows.Close()
	var jobs []Job
	for rows.Next() {
		job, err := scanJob(rows)
		if err != nil {
			return nil, err
		}
		jobs = append(jobs, *job)
	}
	return jobs, mapDBError(rows.Err(), "list jobs conflict", "jobs not found")
}

func (r *PGXRepo) UpdateStatus(ctx context.Context, id uuid.UUID, fromStatus string, toStatus string, fields UpdateStatusFields) error {
	tag, err := r.pool.Exec(ctx, `
		UPDATE jobs
		SET status = $3,
			error_message = CASE WHEN $4::text <> '' THEN $4 ELSE error_message END,
			cancel_requested_at = COALESCE($5, cancel_requested_at),
			queued_at = COALESCE($6, queued_at),
			started_at = COALESCE($7, started_at),
			heartbeat_at = COALESCE($8, heartbeat_at),
			finished_at = COALESCE($9, finished_at),
			updated_at = now()
		WHERE id = $1 AND status = $2
	`, id, fromStatus, toStatus, fields.ErrorMessage, fields.CancelRequestedAt, fields.QueuedAt, fields.StartedAt, fields.HeartbeatAt, fields.FinishedAt)
	if err != nil {
		return mapDBError(err, "update job status conflict", "job not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeConflict, "job status changed", http.StatusConflict, map[string]string{"from": fromStatus, "to": toStatus}, nil)
	}
	return nil
}

func (r *PGXRepo) MarkQueued(ctx context.Context, id uuid.UUID, queuedAt time.Time) error {
	return r.updateStatusAny(ctx, id, []string{JobStatusCreated, JobStatusFailed, JobStatusCompletedWithFailures}, JobStatusQueued, UpdateStatusFields{QueuedAt: &queuedAt})
}

func (r *PGXRepo) MarkRunning(ctx context.Context, id uuid.UUID, startedAt time.Time) error {
	return r.UpdateStatus(ctx, id, JobStatusQueued, JobStatusRunning, UpdateStatusFields{StartedAt: &startedAt, HeartbeatAt: &startedAt})
}

func (r *PGXRepo) MarkHeartbeat(ctx context.Context, id uuid.UUID, heartbeatAt time.Time) error {
	tag, err := r.pool.Exec(ctx, `UPDATE jobs SET heartbeat_at = $2, updated_at = now() WHERE id = $1 AND status IN ('running', 'cancel_requested')`, id, heartbeatAt)
	if err != nil {
		return mapDBError(err, "heartbeat conflict", "job not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeConflict, "job is not running", http.StatusConflict, nil, nil)
	}
	return nil
}

func (r *PGXRepo) MarkSucceeded(ctx context.Context, id uuid.UUID, finishedAt time.Time) error {
	return r.UpdateStatus(ctx, id, JobStatusRunning, JobStatusSucceeded, UpdateStatusFields{FinishedAt: &finishedAt})
}

func (r *PGXRepo) MarkCompletedWithFailures(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error {
	return r.UpdateStatus(ctx, id, JobStatusRunning, JobStatusCompletedWithFailures, UpdateStatusFields{FinishedAt: &finishedAt, ErrorMessage: errMsg})
}

func (r *PGXRepo) MarkFailed(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error {
	return r.updateStatusAny(ctx, id, []string{JobStatusRunning, JobStatusCancelRequested}, JobStatusFailed, UpdateStatusFields{FinishedAt: &finishedAt, ErrorMessage: errMsg})
}

func (r *PGXRepo) MarkEnqueueFailed(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error {
	return r.updateStatusAny(ctx, id, []string{JobStatusCreated, JobStatusQueued}, JobStatusFailed, UpdateStatusFields{FinishedAt: &finishedAt, ErrorMessage: errMsg})
}

func (r *PGXRepo) RequestCancel(ctx context.Context, id uuid.UUID, t time.Time) error {
	return r.UpdateStatus(ctx, id, JobStatusRunning, JobStatusCancelRequested, UpdateStatusFields{CancelRequestedAt: &t})
}

func (r *PGXRepo) MarkCanceled(ctx context.Context, id uuid.UUID, finishedAt time.Time) error {
	return r.updateStatusAny(ctx, id, []string{JobStatusCreated, JobStatusQueued, JobStatusRunning, JobStatusCancelRequested}, JobStatusCanceled, UpdateStatusFields{FinishedAt: &finishedAt})
}

func (r *PGXRepo) IncrementAttempt(ctx context.Context, id uuid.UUID) error {
	tag, err := r.pool.Exec(ctx, `UPDATE jobs SET attempt = attempt + 1, updated_at = now() WHERE id = $1`, id)
	if err != nil {
		return mapDBError(err, "increment attempt conflict", "job not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "job not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXRepo) updateStatusAny(ctx context.Context, id uuid.UUID, from []string, to string, fields UpdateStatusFields) error {
	tag, err := r.pool.Exec(ctx, `
		UPDATE jobs
		SET status = $3,
			error_message = CASE WHEN $4::text <> '' THEN $4 ELSE error_message END,
			cancel_requested_at = COALESCE($5, cancel_requested_at),
			queued_at = COALESCE($6, queued_at),
			started_at = COALESCE($7, started_at),
			heartbeat_at = COALESCE($8, heartbeat_at),
			finished_at = COALESCE($9, finished_at),
			updated_at = now()
		WHERE id = $1 AND status = ANY($2)
	`, id, from, to, fields.ErrorMessage, fields.CancelRequestedAt, fields.QueuedAt, fields.StartedAt, fields.HeartbeatAt, fields.FinishedAt)
	if err != nil {
		return mapDBError(err, "update job status conflict", "job not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeConflict, "job status changed", http.StatusConflict, nil, nil)
	}
	return nil
}

func scanJob(row pgx.Row) (*Job, error) {
	var job Job
	var payload []byte
	err := row.Scan(
		&job.ID, &job.WorkspaceID, &job.JobType, &job.ResourceType, &job.ResourceID,
		&job.Status, &job.Priority, &job.Attempt, &job.MaxAttempts, &payload,
		&job.ErrorMessage, &job.CancelRequestedAt, &job.QueuedAt, &job.StartedAt,
		&job.HeartbeatAt, &job.FinishedAt, &job.CreatedBy, &job.CreatedAt, &job.UpdatedAt,
	)
	if err != nil {
		return nil, mapDBError(err, "job conflict", "job not found")
	}
	if len(payload) > 0 {
		if err := json.Unmarshal(payload, &job.Payload); err != nil {
			return nil, httpx.NewAppError(httpx.CodeInternal, "decode job payload failed", http.StatusInternalServerError, nil, err)
		}
	}
	if job.Payload == nil {
		job.Payload = map[string]any{}
	}
	return &job, nil
}

func mapDBError(err error, conflictMessage string, notFoundMessage string) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return httpx.NewAppError(httpx.CodeNotFound, notFoundMessage, http.StatusNotFound, nil, err)
	}
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		switch pgErr.Code {
		case "23505":
			return httpx.NewAppError(httpx.CodeConflict, conflictMessage, http.StatusConflict, nil, err)
		case "23503":
			return httpx.NewAppError(httpx.CodeNotFound, notFoundMessage, http.StatusNotFound, nil, err)
		}
	}
	return httpx.NewAppError(httpx.CodeInternal, "database operation failed", http.StatusInternalServerError, nil, err)
}
