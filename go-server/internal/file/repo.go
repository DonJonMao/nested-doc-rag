package file

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
	Create(ctx context.Context, file File) error
	GetByID(ctx context.Context, id uuid.UUID) (*File, error)
	ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, category string, limit int, offset int) ([]File, error)
	SoftDelete(ctx context.Context, id uuid.UUID, deletedAt time.Time) error
	ExistsByHash(ctx context.Context, workspaceID uuid.UUID, sha256 string) (bool, error)
}

type PGXRepo struct {
	pool *pgxpool.Pool
}

func NewPGXRepo(pool *pgxpool.Pool) *PGXRepo {
	return &PGXRepo{pool: pool}
}

func (r *PGXRepo) Create(ctx context.Context, file File) error {
	if file.CreatedAt.IsZero() {
		file.CreatedAt = time.Now().UTC()
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO files (
			id, workspace_id, filename, original_filename, object_key, file_size, mime_type,
			sha256, file_category, status, created_by, created_at, deleted_at
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
	`, file.ID, file.WorkspaceID, file.Filename, file.OriginalFilename, file.ObjectKey, file.FileSize, file.MIMEType, file.SHA256, file.FileCategory, file.Status, file.CreatedBy, file.CreatedAt, file.DeletedAt)
	return mapDBError(err, "file already exists", "file not found")
}

func (r *PGXRepo) GetByID(ctx context.Context, id uuid.UUID) (*File, error) {
	return scanFile(r.pool.QueryRow(ctx, `
		SELECT id, workspace_id, filename, original_filename, object_key, file_size, COALESCE(mime_type, ''),
			sha256, file_category, status, created_by, created_at, deleted_at
		FROM files WHERE id = $1
	`, id))
}

func (r *PGXRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, category string, limit int, offset int) ([]File, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	var rows pgx.Rows
	var err error
	if category != "" {
		rows, err = r.pool.Query(ctx, `
			SELECT id, workspace_id, filename, original_filename, object_key, file_size, COALESCE(mime_type, ''),
				sha256, file_category, status, created_by, created_at, deleted_at
			FROM files
			WHERE workspace_id = $1 AND file_category = $2 AND status = 'active'
			ORDER BY created_at DESC LIMIT $3 OFFSET $4
		`, workspaceID, category, limit, offset)
	} else {
		rows, err = r.pool.Query(ctx, `
			SELECT id, workspace_id, filename, original_filename, object_key, file_size, COALESCE(mime_type, ''),
				sha256, file_category, status, created_by, created_at, deleted_at
			FROM files
			WHERE workspace_id = $1 AND status = 'active'
			ORDER BY created_at DESC LIMIT $2 OFFSET $3
		`, workspaceID, limit, offset)
	}
	if err != nil {
		return nil, mapDBError(err, "list files conflict", "files not found")
	}
	defer rows.Close()
	var files []File
	for rows.Next() {
		file, err := scanFile(rows)
		if err != nil {
			return nil, err
		}
		files = append(files, *file)
	}
	return files, mapDBError(rows.Err(), "list files conflict", "files not found")
}

func (r *PGXRepo) SoftDelete(ctx context.Context, id uuid.UUID, deletedAt time.Time) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return mapDBError(err, "delete file conflict", "file not found")
	}
	defer func() { _ = tx.Rollback(context.Background()) }()
	// Build snapshots and fill template pins acquire the same row lock before
	// recording their references. The loser of a delete/pin race rechecks state.
	var status string
	if err := tx.QueryRow(ctx, `SELECT status FROM files WHERE id=$1 FOR UPDATE`, id).Scan(&status); err != nil {
		return mapDBError(err, "delete file conflict", "file not found")
	}
	if status == FileStatusDeleted {
		return httpx.NewAppError(httpx.CodeNotFound, "file not found", http.StatusNotFound, nil, nil)
	}
	var pinned bool
	err = tx.QueryRow(ctx, `
		SELECT EXISTS(SELECT 1 FROM knowledge_version_source_pins WHERE file_id=$1)
		OR EXISTS(SELECT 1 FROM fill_run_template_pins WHERE file_id=$1)
		OR EXISTS(
			SELECT 1 FROM knowledge_documents d
			JOIN knowledge_bases kb ON kb.id=d.knowledge_base_id
			JOIN knowledge_index_versions v ON v.knowledge_base_id=kb.id
			WHERE d.file_id=$1 AND v.storage_contract='legacy_unversioned'
			AND (kb.current_index_version_id=v.id OR EXISTS(
				SELECT 1 FROM fill_run_index_pins p WHERE p.index_version_id=v.id
			))
		)
	`, id).Scan(&pinned)
	if err != nil {
		return mapDBError(err, "check file pins conflict", "file not found")
	}
	if pinned {
		return httpx.NewAppError(httpx.CodeConflict, "file is pinned by an index version or fill run", http.StatusConflict, map[string]string{"reason": "file_pinned"}, nil)
	}
	tag, err := tx.Exec(ctx, `
		UPDATE files SET status = 'deleted', deleted_at = $2 WHERE id = $1 AND status <> 'deleted'
	`, id, deletedAt)
	if err != nil {
		return mapDBError(err, "delete file conflict", "file not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "file not found", http.StatusNotFound, nil, nil)
	}
	return mapDBError(tx.Commit(ctx), "delete file commit conflict", "file not found")
}

func (r *PGXRepo) ExistsByHash(ctx context.Context, workspaceID uuid.UUID, sha256 string) (bool, error) {
	var exists bool
	err := r.pool.QueryRow(ctx, `
		SELECT EXISTS(SELECT 1 FROM files WHERE workspace_id = $1 AND sha256 = $2 AND status = 'active')
	`, workspaceID, sha256).Scan(&exists)
	if err != nil {
		return false, mapDBError(err, "file hash conflict", "file not found")
	}
	return exists, nil
}

func scanFile(row pgx.Row) (*File, error) {
	var file File
	err := row.Scan(
		&file.ID,
		&file.WorkspaceID,
		&file.Filename,
		&file.OriginalFilename,
		&file.ObjectKey,
		&file.FileSize,
		&file.MIMEType,
		&file.SHA256,
		&file.FileCategory,
		&file.Status,
		&file.CreatedBy,
		&file.CreatedAt,
		&file.DeletedAt,
	)
	if err != nil {
		return nil, mapDBError(err, "file conflict", "file not found")
	}
	return &file, nil
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
