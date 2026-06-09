package tests

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestAuthServiceLoginSuccess(t *testing.T) {
	service, users, refreshes, _ := newAuthServiceFixture(t)
	hash, err := auth.HashPassword("password123")
	require.NoError(t, err)
	user := auth.User{ID: uuid.New(), Username: "admin", PasswordHash: hash, Status: auth.UserStatusActive}
	users.addUser(user, []string{auth.RoleAdmin})

	result, err := service.Login(context.Background(), "admin", "password123", "127.0.0.1", "test")

	require.NoError(t, err)
	require.NotEmpty(t, result.AccessToken)
	require.NotEmpty(t, result.RefreshToken)
	require.Equal(t, "admin", result.User.Username)
	require.Len(t, refreshes.tokens, 1)
}

func TestAuthServiceLoginWrongPassword(t *testing.T) {
	service, users, _, _ := newAuthServiceFixture(t)
	hash, err := auth.HashPassword("password123")
	require.NoError(t, err)
	users.addUser(auth.User{ID: uuid.New(), Username: "admin", PasswordHash: hash, Status: auth.UserStatusActive}, []string{auth.RoleAdmin})

	_, err = service.Login(context.Background(), "admin", "wrong-password", "127.0.0.1", "test")

	requireAppError(t, err, httpx.CodeUnauthorized, http.StatusUnauthorized)
}

func TestAuthServiceDisabledUser(t *testing.T) {
	service, users, _, _ := newAuthServiceFixture(t)
	hash, err := auth.HashPassword("password123")
	require.NoError(t, err)
	users.addUser(auth.User{ID: uuid.New(), Username: "admin", PasswordHash: hash, Status: auth.UserStatusDisabled}, []string{auth.RoleAdmin})

	_, err = service.Login(context.Background(), "admin", "password123", "127.0.0.1", "test")

	requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
}

func TestAuthServiceRefreshSuccessRotatesToken(t *testing.T) {
	service, users, refreshes, _ := newAuthServiceFixture(t)
	hash, err := auth.HashPassword("password123")
	require.NoError(t, err)
	user := auth.User{ID: uuid.New(), Username: "admin", PasswordHash: hash, Status: auth.UserStatusActive}
	users.addUser(user, []string{auth.RoleAdmin})
	login, err := service.Login(context.Background(), "admin", "password123", "127.0.0.1", "test")
	require.NoError(t, err)

	refreshed, err := service.Refresh(context.Background(), login.RefreshToken)

	require.NoError(t, err)
	require.NotEmpty(t, refreshed.AccessToken)
	require.NotEqual(t, login.RefreshToken, refreshed.RefreshToken)
	require.Len(t, refreshes.tokens, 2)
	require.NotNil(t, refreshes.tokens[auth.HashRefreshToken(login.RefreshToken)].RevokedAt)
}

func TestAuthServiceLogoutRevokesToken(t *testing.T) {
	service, users, refreshes, _ := newAuthServiceFixture(t)
	hash, err := auth.HashPassword("password123")
	require.NoError(t, err)
	user := auth.User{ID: uuid.New(), Username: "admin", PasswordHash: hash, Status: auth.UserStatusActive}
	users.addUser(user, []string{auth.RoleAdmin})
	login, err := service.Login(context.Background(), "admin", "password123", "127.0.0.1", "test")
	require.NoError(t, err)

	require.NoError(t, service.Logout(context.Background(), login.RefreshToken))

	require.NotNil(t, refreshes.tokens[auth.HashRefreshToken(login.RefreshToken)].RevokedAt)
}

func newAuthServiceFixture(t *testing.T) (*auth.Service, *fakeUserRepo, *fakeRefreshRepo, *fakeAuditRepo) {
	t.Helper()
	users := newFakeUserRepo()
	refreshes := newFakeRefreshRepo()
	audits := &fakeAuditRepo{}
	manager, err := auth.NewTokenManager("test-secret", time.Minute)
	require.NoError(t, err)
	return auth.NewService(users, users, refreshes, manager, time.Hour, newAuditServiceForTest(audits)), users, refreshes, audits
}

func newAuditServiceForTest(repo *fakeAuditRepo) *audit.Service {
	return audit.NewService(repo, zap.NewNop())
}
