package auth

import (
	"context"
	"errors"
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type PGXRefreshTokenRepo struct {
	pool *pgxpool.Pool
}

func NewPGXRefreshTokenRepo(pool *pgxpool.Pool) *PGXRefreshTokenRepo {
	return &PGXRefreshTokenRepo{pool: pool}
}

func (r *PGXRefreshTokenRepo) Create(ctx context.Context, token RefreshToken) error {
	_, err := r.pool.Exec(ctx, `
		INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at, revoked_at, created_at)
		VALUES ($1, $2, $3, $4, $5, now())
	`, token.ID, token.UserID, token.TokenHash, token.ExpiresAt, token.RevokedAt)
	return mapRefreshDBError(err, "create refresh token failed")
}

func (r *PGXRefreshTokenRepo) GetByHash(ctx context.Context, hash string) (*RefreshToken, error) {
	var token RefreshToken
	err := r.pool.QueryRow(ctx, `
		SELECT id, user_id, token_hash, expires_at, revoked_at, created_at
		FROM refresh_tokens WHERE token_hash = $1
	`, hash).Scan(&token.ID, &token.UserID, &token.TokenHash, &token.ExpiresAt, &token.RevokedAt, &token.CreatedAt)
	if err != nil {
		return nil, mapRefreshDBError(err, "refresh token not found")
	}
	return &token, nil
}

func (r *PGXRefreshTokenRepo) Revoke(ctx context.Context, id uuid.UUID) error {
	tag, err := r.pool.Exec(ctx, `UPDATE refresh_tokens SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL`, id)
	if err != nil {
		return mapRefreshDBError(err, "revoke refresh token failed")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "refresh token not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXRefreshTokenRepo) RevokeAllForUser(ctx context.Context, userID uuid.UUID) error {
	_, err := r.pool.Exec(ctx, `UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL`, userID)
	return mapRefreshDBError(err, "revoke refresh tokens failed")
}

func mapRefreshDBError(err error, message string) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return httpx.NewAppError(httpx.CodeNotFound, message, http.StatusNotFound, nil, err)
	}
	return httpx.NewAppError(httpx.CodeInternal, "database operation failed", http.StatusInternalServerError, nil, err)
}
