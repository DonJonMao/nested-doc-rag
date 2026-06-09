package tests

import (
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/stretchr/testify/require"
)

func TestHashAndVerifyPassword(t *testing.T) {
	hash, err := auth.HashPassword("password123")
	require.NoError(t, err)
	require.True(t, auth.VerifyPassword(hash, "password123"))
	require.False(t, auth.VerifyPassword(hash, "wrong-password"))
}

func TestWeakPasswordRejected(t *testing.T) {
	require.Error(t, auth.ValidatePasswordStrength(""))
	require.Error(t, auth.ValidatePasswordStrength("short"))
	require.NoError(t, auth.ValidatePasswordStrength("strong123"))
}
