package runevent

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
	Create(ctx context.Context, event RunEvent) (*RunEvent, error)
	ListByRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, afterSequence int64, limit int) ([]RunEvent, error)
	LastSequence(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID) (int64, error)
}

type PGXRepo struct {
	pool *pgxpool.Pool
}

func NewPGXRepo(pool *pgxpool.Pool) *PGXRepo {
	return &PGXRepo{pool: pool}
}

func (r *PGXRepo) Create(ctx context.Context, event RunEvent) (*RunEvent, error) {
	if event.ID == uuid.Nil {
		event.ID = uuid.New()
	}
	if event.CreatedAt.IsZero() {
		event.CreatedAt = time.Now().UTC()
	}
	payload, err := json.Marshal(event.Payload)
	if err != nil {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid run event payload", http.StatusBadRequest, nil, err)
	}
	err = r.pool.QueryRow(ctx, `
		INSERT INTO run_events (id, workspace_id, run_id, job_id, event_type, payload_json, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		RETURNING sequence, created_at
	`, event.ID, event.WorkspaceID, event.RunID, event.JobID, event.EventType, payload, event.CreatedAt).Scan(&event.Sequence, &event.CreatedAt)
	if err != nil {
		return nil, mapDBError(err, "create run event conflict", "run event not found")
	}
	return &event, nil
}

func (r *PGXRepo) ListByRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, afterSequence int64, limit int) ([]RunEvent, error) {
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	rows, err := r.pool.Query(ctx, `
		SELECT id, workspace_id, run_id, job_id, event_type, sequence, payload_json, created_at
		FROM run_events
		WHERE workspace_id = $1 AND run_id = $2 AND sequence > $3
		ORDER BY sequence ASC LIMIT $4
	`, workspaceID, runID, afterSequence, limit)
	if err != nil {
		return nil, mapDBError(err, "list run events conflict", "run events not found")
	}
	defer rows.Close()
	var events []RunEvent
	for rows.Next() {
		event, err := scanRunEvent(rows)
		if err != nil {
			return nil, err
		}
		events = append(events, *event)
	}
	return events, mapDBError(rows.Err(), "list run events conflict", "run events not found")
}

func (r *PGXRepo) LastSequence(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID) (int64, error) {
	var seq int64
	err := r.pool.QueryRow(ctx, `
		SELECT COALESCE(MAX(sequence), 0) FROM run_events WHERE workspace_id = $1 AND run_id = $2
	`, workspaceID, runID).Scan(&seq)
	if err != nil {
		return 0, mapDBError(err, "read last sequence conflict", "run events not found")
	}
	return seq, nil
}

func scanRunEvent(row pgx.Row) (*RunEvent, error) {
	var event RunEvent
	var payload []byte
	err := row.Scan(&event.ID, &event.WorkspaceID, &event.RunID, &event.JobID, &event.EventType, &event.Sequence, &payload, &event.CreatedAt)
	if err != nil {
		return nil, mapDBError(err, "run event conflict", "run event not found")
	}
	if len(payload) > 0 {
		if err := json.Unmarshal(payload, &event.Payload); err != nil {
			return nil, httpx.NewAppError(httpx.CodeInternal, "decode run event payload failed", http.StatusInternalServerError, nil, err)
		}
	}
	return &event, nil
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
