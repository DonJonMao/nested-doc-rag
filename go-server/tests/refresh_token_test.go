package tests

import (
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/stretchr/testify/require"
)

func TestGenerateRefreshTokenAndVerify(t *testing.T) {
	plain, hash, err := auth.GenerateRefreshToken()
	require.NoError(t, err)
	require.NotEmpty(t, plain)
	require.NotEqual(t, plain, hash)
	require.True(t, auth.VerifyRefreshToken(plain, hash))
	require.False(t, auth.VerifyRefreshToken("wrong-token", hash))
}
