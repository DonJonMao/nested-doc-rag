package workspace

import (
	"context"
	"errors"
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

type PGXRepo struct {
	pool *pgxpool.Pool
}

func NewPGXRepo(pool *pgxpool.Pool) *PGXRepo {
	return &PGXRepo{pool: pool}
}

func (r *PGXRepo) Create(ctx context.Context, workspace Workspace) error {
	_, err := r.pool.Exec(ctx, `
		INSERT INTO workspaces (id, name, description, created_by, created_at, updated_at)
		VALUES ($1, $2, $3, $4, now(), now())
	`, workspace.ID, workspace.Name, workspace.Description, workspace.CreatedBy)
	return mapDBError(err, "workspace conflict", "workspace not found")
}

func (r *PGXRepo) GetByID(ctx context.Context, id uuid.UUID) (*Workspace, error) {
	var ws Workspace
	err := r.pool.QueryRow(ctx, `
		SELECT id, name, COALESCE(description, ''), created_by, created_at, updated_at
		FROM workspaces WHERE id = $1
	`, id).Scan(&ws.ID, &ws.Name, &ws.Description, &ws.CreatedBy, &ws.CreatedAt, &ws.UpdatedAt)
	if err != nil {
		return nil, mapDBError(err, "workspace conflict", "workspace not found")
	}
	return &ws, nil
}

func (r *PGXRepo) ListByUser(ctx context.Context, userID uuid.UUID) ([]Workspace, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT w.id, w.name, COALESCE(w.description, ''), w.created_by, w.created_at, w.updated_at
		FROM workspaces w
		JOIN workspace_members wm ON wm.workspace_id = w.id
		WHERE wm.user_id = $1
		ORDER BY w.created_at DESC
	`, userID)
	if err != nil {
		return nil, mapDBError(err, "list workspaces conflict", "workspaces not found")
	}
	defer rows.Close()
	var workspaces []Workspace
	for rows.Next() {
		var ws Workspace
		if err := rows.Scan(&ws.ID, &ws.Name, &ws.Description, &ws.CreatedBy, &ws.CreatedAt, &ws.UpdatedAt); err != nil {
			return nil, mapDBError(err, "list workspaces conflict", "workspaces not found")
		}
		workspaces = append(workspaces, ws)
	}
	return workspaces, mapDBError(rows.Err(), "list workspaces conflict", "workspaces not found")
}

func (r *PGXRepo) ListAll(ctx context.Context) ([]Workspace, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, name, COALESCE(description, ''), created_by, created_at, updated_at
		FROM workspaces ORDER BY created_at DESC
	`)
	if err != nil {
		return nil, mapDBError(err, "list workspaces conflict", "workspaces not found")
	}
	defer rows.Close()
	var workspaces []Workspace
	for rows.Next() {
		var ws Workspace
		if err := rows.Scan(&ws.ID, &ws.Name, &ws.Description, &ws.CreatedBy, &ws.CreatedAt, &ws.UpdatedAt); err != nil {
			return nil, mapDBError(err, "list workspaces conflict", "workspaces not found")
		}
		workspaces = append(workspaces, ws)
	}
	return workspaces, mapDBError(rows.Err(), "list workspaces conflict", "workspaces not found")
}

func (r *PGXRepo) AddMember(ctx context.Context, workspaceID uuid.UUID, userID uuid.UUID, role string) error {
	_, err := r.pool.Exec(ctx, `
		INSERT INTO workspace_members (workspace_id, user_id, role, created_at)
		VALUES ($1, $2, $3, now())
		ON CONFLICT (workspace_id, user_id) DO UPDATE SET role = EXCLUDED.role
	`, workspaceID, userID, role)
	return mapDBError(err, "workspace member conflict", "workspace or user not found")
}

func (r *PGXRepo) GetMemberRole(ctx context.Context, workspaceID uuid.UUID, userID uuid.UUID) (string, error) {
	var role string
	err := r.pool.QueryRow(ctx, `
		SELECT role FROM workspace_members WHERE workspace_id = $1 AND user_id = $2
	`, workspaceID, userID).Scan(&role)
	if err != nil {
		return "", mapDBError(err, "workspace member conflict", "workspace membership not found")
	}
	return role, nil
}

func (r *PGXRepo) ListMembers(ctx context.Context, workspaceID uuid.UUID) ([]WorkspaceMemberView, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT wm.workspace_id, wm.user_id, u.username, COALESCE(u.display_name, ''), wm.role, wm.created_at
		FROM workspace_members wm
		JOIN users u ON u.id = wm.user_id
		WHERE wm.workspace_id = $1
		ORDER BY wm.created_at ASC
	`, workspaceID)
	if err != nil {
		return nil, mapDBError(err, "list workspace members conflict", "workspace members not found")
	}
	defer rows.Close()
	var members []WorkspaceMemberView
	for rows.Next() {
		var member WorkspaceMemberView
		if err := rows.Scan(&member.WorkspaceID, &member.UserID, &member.Username, &member.DisplayName, &member.Role, &member.CreatedAt); err != nil {
			return nil, mapDBError(err, "list workspace members conflict", "workspace members not found")
		}
		members = append(members, member)
	}
	return members, mapDBError(rows.Err(), "list workspace members conflict", "workspace members not found")
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
