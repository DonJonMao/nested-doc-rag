package user

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
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

func (r *PGXRepo) Create(ctx context.Context, user auth.User) error {
	if user.ID == uuid.Nil {
		user.ID = uuid.New()
	}
	now := time.Now().UTC()
	if user.CreatedAt.IsZero() {
		user.CreatedAt = now
	}
	if user.UpdatedAt.IsZero() {
		user.UpdatedAt = now
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO users (id, username, password_hash, display_name, email, status, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
	`, user.ID, user.Username, user.PasswordHash, user.DisplayName, user.Email, user.Status, user.CreatedAt, user.UpdatedAt)
	return mapDBError(err, "user already exists", "user not found")
}

func (r *PGXRepo) GetByID(ctx context.Context, id uuid.UUID) (*auth.User, error) {
	return r.scanUser(r.pool.QueryRow(ctx, `
		SELECT id, username, password_hash, COALESCE(display_name, ''), COALESCE(email, ''), status, created_at, updated_at
		FROM users WHERE id = $1
	`, id))
}

func (r *PGXRepo) GetByUsername(ctx context.Context, username string) (*auth.User, error) {
	return r.scanUser(r.pool.QueryRow(ctx, `
		SELECT id, username, password_hash, COALESCE(display_name, ''), COALESCE(email, ''), status, created_at, updated_at
		FROM users WHERE username = $1
	`, username))
}

func (r *PGXRepo) List(ctx context.Context, limit int, offset int) ([]auth.User, error) {
	if limit <= 0 || limit > 200 {
		limit = 100
	}
	rows, err := r.pool.Query(ctx, `
		SELECT id, username, password_hash, COALESCE(display_name, ''), COALESCE(email, ''), status, created_at, updated_at
		FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2
	`, limit, offset)
	if err != nil {
		return nil, mapDBError(err, "list users conflict", "users not found")
	}
	defer rows.Close()
	var users []auth.User
	for rows.Next() {
		var u auth.User
		if err := rows.Scan(&u.ID, &u.Username, &u.PasswordHash, &u.DisplayName, &u.Email, &u.Status, &u.CreatedAt, &u.UpdatedAt); err != nil {
			return nil, mapDBError(err, "list users conflict", "users not found")
		}
		users = append(users, u)
	}
	return users, mapDBError(rows.Err(), "list users conflict", "users not found")
}

func (r *PGXRepo) SetStatus(ctx context.Context, id uuid.UUID, status string) error {
	tag, err := r.pool.Exec(ctx, `UPDATE users SET status = $2, updated_at = now() WHERE id = $1`, id, status)
	if err != nil {
		return mapDBError(err, "set user status conflict", "user not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "user not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXRepo) AssignRole(ctx context.Context, userID uuid.UUID, roleName string) error {
	tag, err := r.pool.Exec(ctx, `
		INSERT INTO user_roles (user_id, role_id)
		SELECT $1, id FROM roles WHERE name = $2
		ON CONFLICT DO NOTHING
	`, userID, roleName)
	if err != nil {
		return mapDBError(err, "assign role conflict", "user or role not found")
	}
	if tag.RowsAffected() == 0 {
		if _, err := r.GetByID(ctx, userID); err != nil {
			return err
		}
		if _, err := r.GetByName(ctx, roleName); err != nil {
			return err
		}
	}
	return nil
}

func (r *PGXRepo) ListRoleNames(ctx context.Context, userID uuid.UUID) ([]string, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT roles.name
		FROM user_roles
		JOIN roles ON roles.id = user_roles.role_id
		WHERE user_roles.user_id = $1
		ORDER BY roles.name
	`, userID)
	if err != nil {
		return nil, mapDBError(err, "list roles conflict", "roles not found")
	}
	defer rows.Close()
	var roles []string
	for rows.Next() {
		var role string
		if err := rows.Scan(&role); err != nil {
			return nil, mapDBError(err, "list roles conflict", "roles not found")
		}
		roles = append(roles, role)
	}
	return roles, mapDBError(rows.Err(), "list roles conflict", "roles not found")
}

func (r *PGXRepo) EnsureDefaultRoles(ctx context.Context) error {
	_, err := r.pool.Exec(ctx, `
		INSERT INTO roles (id, name, description)
		VALUES
			('00000000-0000-0000-0000-000000000001', 'admin', 'Global administrator'),
			('00000000-0000-0000-0000-000000000002', 'operator', 'Platform operator'),
			('00000000-0000-0000-0000-000000000003', 'reviewer', 'Review queue operator'),
			('00000000-0000-0000-0000-000000000004', 'viewer', 'Read-only viewer')
		ON CONFLICT (name) DO NOTHING
	`)
	return mapDBError(err, "ensure roles conflict", "roles not found")
}

func (r *PGXRepo) GetByName(ctx context.Context, name string) (*auth.Role, error) {
	var role auth.Role
	err := r.pool.QueryRow(ctx, `
		SELECT id, name, COALESCE(description, ''), created_at FROM roles WHERE name = $1
	`, name).Scan(&role.ID, &role.Name, &role.Description, &role.CreatedAt)
	if err != nil {
		return nil, mapDBError(err, "role conflict", "role not found")
	}
	return &role, nil
}

func (r *PGXRepo) scanUser(row pgx.Row) (*auth.User, error) {
	var user auth.User
	err := row.Scan(&user.ID, &user.Username, &user.PasswordHash, &user.DisplayName, &user.Email, &user.Status, &user.CreatedAt, &user.UpdatedAt)
	if err != nil {
		return nil, mapDBError(err, "user conflict", "user not found")
	}
	return &user, nil
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
