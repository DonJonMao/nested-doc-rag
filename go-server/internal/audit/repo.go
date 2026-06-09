package audit

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Repo interface {
	Create(ctx context.Context, log AuditLog) error
}

type PGXRepo struct {
	pool *pgxpool.Pool
}

func NewPGXRepo(pool *pgxpool.Pool) *PGXRepo {
	return &PGXRepo{pool: pool}
}

func (r *PGXRepo) Create(ctx context.Context, log AuditLog) error {
	payload, err := json.Marshal(log.Payload)
	if err != nil {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "invalid audit payload", 400, nil, err)
	}
	createdAt := log.CreatedAt
	if createdAt.IsZero() {
		createdAt = time.Now().UTC()
	}
	_, err = r.pool.Exec(ctx, `
		INSERT INTO audit_logs (
			id, workspace_id, user_id, action, resource_type, resource_id, ip, user_agent, payload_json, created_at
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
	`, log.ID, log.WorkspaceID, log.UserID, log.Action, log.ResourceType, log.ResourceID, log.IP, log.UserAgent, payload, createdAt)
	if err != nil {
		return httpx.NewAppError(httpx.CodeInternal, "create audit log failed", 500, nil, fmt.Errorf("insert audit log: %w", err))
	}
	return nil
}
