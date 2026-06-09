package tests

import (
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestJWTGenerateAndParse(t *testing.T) {
	manager, err := auth.NewTokenManager("test-secret", time.Minute)
	require.NoError(t, err)
	user := auth.User{ID: uuid.New(), Username: "admin"}

	token, expiresAt, err := manager.GenerateAccessToken(user, []string{auth.RoleAdmin})
	require.NoError(t, err)
	require.True(t, expiresAt.After(time.Now()))

	claims, err := manager.ParseAccessToken(token)
	require.NoError(t, err)
	require.Equal(t, user.ID.String(), claims.UserID)
	require.Equal(t, "admin", claims.Username)
	require.Equal(t, []string{auth.RoleAdmin}, claims.Roles)
}

func TestJWTExpiredTokenRejected(t *testing.T) {
	manager, err := auth.NewTokenManager("test-secret", time.Nanosecond)
	require.NoError(t, err)
	token, _, err := manager.GenerateAccessToken(auth.User{ID: uuid.New(), Username: "admin"}, []string{auth.RoleAdmin})
	require.NoError(t, err)
	time.Sleep(time.Millisecond)
	_, err = manager.ParseAccessToken(token)
	require.Error(t, err)
}

func TestJWTInvalidSignatureRejected(t *testing.T) {
	manager, err := auth.NewTokenManager("test-secret", time.Minute)
	require.NoError(t, err)
	other, err := auth.NewTokenManager("other-secret", time.Minute)
	require.NoError(t, err)

	token, _, err := manager.GenerateAccessToken(auth.User{ID: uuid.New(), Username: "admin"}, []string{auth.RoleAdmin})
	require.NoError(t, err)

	_, err = other.ParseAccessToken(token)
	require.Error(t, err)
}
