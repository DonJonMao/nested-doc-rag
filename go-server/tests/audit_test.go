package tests

import (
	"context"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestAuditCreateAndSanitizePayload(t *testing.T) {
	repo := &fakeAuditRepo{}
	service := audit.NewService(repo, zap.NewNop())

	service.Record(context.Background(), audit.AuditLog{
		Action:  "auth.login_failed",
		Payload: map[string]any{"username": "admin", "password": "secret", "refresh_token": "secret"},
	})

	require.Len(t, repo.logs, 1)
	require.Equal(t, "admin", repo.logs[0].Payload["username"])
	require.NotContains(t, repo.logs[0].Payload, "password")
	require.NotContains(t, repo.logs[0].Payload, "refresh_token")
}

type fakeAuditRepo struct {
	logs []audit.AuditLog
	err  error
}

func (f *fakeAuditRepo) Create(ctx context.Context, log audit.AuditLog) error {
	if f.err != nil {
		return f.err
	}
	f.logs = append(f.logs, log)
	return nil
}
