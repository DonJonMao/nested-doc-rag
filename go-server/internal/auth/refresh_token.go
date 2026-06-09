package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"net/http"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
)

func GenerateRefreshToken() (plain string, hash string, err error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", "", httpx.NewAppError(httpx.CodeInternal, "generate refresh token failed", http.StatusInternalServerError, nil, err)
	}
	plain = base64.RawURLEncoding.EncodeToString(buf)
	return plain, HashRefreshToken(plain), nil
}

func HashRefreshToken(token string) string {
	sum := sha256.Sum256([]byte(strings.TrimSpace(token)))
	return hex.EncodeToString(sum[:])
}

func VerifyRefreshToken(token string, hash string) bool {
	if strings.TrimSpace(token) == "" || strings.TrimSpace(hash) == "" {
		return false
	}
	computed := HashRefreshToken(token)
	return subtle.ConstantTimeCompare([]byte(computed), []byte(hash)) == 1
}
