package artifact

import (
	"context"
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
	Create(ctx context.Context, artifact RunArtifact) error
	GetByID(ctx context.Context, id uuid.UUID) (*RunArtifact, error)
	ListByRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID) ([]RunArtifact, error)
	ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]RunArtifact, error)
}

type PGXRepo struct {
	pool *pgxpool.Pool
}

func NewPGXRepo(pool *pgxpool.Pool) *PGXRepo {
	return &PGXRepo{pool: pool}
}

func (r *PGXRepo) Create(ctx context.Context, artifact RunArtifact) error {
	if artifact.CreatedAt.IsZero() {
		artifact.CreatedAt = time.Now().UTC()
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO run_artifacts (
			id, workspace_id, run_id, artifact_type, filename, object_key, local_path,
			content_type, file_size, sha256, created_by, created_at
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
	`, artifact.ID, artifact.WorkspaceID, artifact.RunID, artifact.ArtifactType, artifact.Filename, artifact.ObjectKey, artifact.LocalPath, artifact.ContentType, artifact.FileSize, artifact.SHA256, artifact.CreatedBy, artifact.CreatedAt)
	return mapDBError(err, "artifact already exists", "artifact not found")
}

func (r *PGXRepo) GetByID(ctx context.Context, id uuid.UUID) (*RunArtifact, error) {
	return scanArtifact(r.pool.QueryRow(ctx, `
		SELECT id, workspace_id, run_id, artifact_type, filename, object_key, COALESCE(local_path, ''),
			COALESCE(content_type, ''), COALESCE(file_size, 0), COALESCE(sha256, ''), created_by, created_at
		FROM run_artifacts WHERE id = $1
	`, id))
}

func (r *PGXRepo) ListByRun(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID) ([]RunArtifact, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, workspace_id, run_id, artifact_type, filename, object_key, COALESCE(local_path, ''),
			COALESCE(content_type, ''), COALESCE(file_size, 0), COALESCE(sha256, ''), created_by, created_at
		FROM run_artifacts
		WHERE workspace_id = $1 AND run_id = $2
		ORDER BY created_at ASC
	`, workspaceID, runID)
	if err != nil {
		return nil, mapDBError(err, "list artifacts conflict", "artifacts not found")
	}
	defer rows.Close()
	return scanArtifacts(rows)
}

func (r *PGXRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]RunArtifact, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := r.pool.Query(ctx, `
		SELECT id, workspace_id, run_id, artifact_type, filename, object_key, COALESCE(local_path, ''),
			COALESCE(content_type, ''), COALESCE(file_size, 0), COALESCE(sha256, ''), created_by, created_at
		FROM run_artifacts
		WHERE workspace_id = $1
		ORDER BY created_at DESC LIMIT $2 OFFSET $3
	`, workspaceID, limit, offset)
	if err != nil {
		return nil, mapDBError(err, "list artifacts conflict", "artifacts not found")
	}
	defer rows.Close()
	return scanArtifacts(rows)
}

func scanArtifacts(rows pgx.Rows) ([]RunArtifact, error) {
	var artifacts []RunArtifact
	for rows.Next() {
		item, err := scanArtifact(rows)
		if err != nil {
			return nil, err
		}
		artifacts = append(artifacts, *item)
	}
	return artifacts, mapDBError(rows.Err(), "list artifacts conflict", "artifacts not found")
}

func scanArtifact(row pgx.Row) (*RunArtifact, error) {
	var item RunArtifact
	err := row.Scan(
		&item.ID,
		&item.WorkspaceID,
		&item.RunID,
		&item.ArtifactType,
		&item.Filename,
		&item.ObjectKey,
		&item.LocalPath,
		&item.ContentType,
		&item.FileSize,
		&item.SHA256,
		&item.CreatedBy,
		&item.CreatedAt,
	)
	if err != nil {
		return nil, mapDBError(err, "artifact conflict", "artifact not found")
	}
	return &item, nil
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
