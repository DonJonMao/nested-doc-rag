package auth

import (
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/golang-jwt/jwt/v5"
)

type TokenClaims struct {
	UserID   string   `json:"user_id"`
	Username string   `json:"username"`
	Roles    []string `json:"roles"`
	jwt.RegisteredClaims
}

type TokenManager struct {
	secret    []byte
	accessTTL time.Duration
}

func NewTokenManager(secret string, accessTTL time.Duration) (*TokenManager, error) {
	if strings.TrimSpace(secret) == "" {
		return nil, httpx.NewAppError(httpx.CodeInternal, "jwt secret is not configured", http.StatusInternalServerError, nil, nil)
	}
	if accessTTL <= 0 {
		return nil, httpx.NewAppError(httpx.CodeInternal, "access token ttl must be greater than 0", http.StatusInternalServerError, nil, nil)
	}
	return &TokenManager{secret: []byte(secret), accessTTL: accessTTL}, nil
}

func (m *TokenManager) GenerateAccessToken(user User, roles []string) (string, time.Time, error) {
	now := time.Now().UTC()
	expiresAt := now.Add(m.accessTTL)
	claims := TokenClaims{
		UserID:   user.ID.String(),
		Username: user.Username,
		Roles:    roles,
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   user.ID.String(),
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(expiresAt),
		},
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	signed, err := token.SignedString(m.secret)
	if err != nil {
		return "", time.Time{}, httpx.NewAppError(httpx.CodeInternal, "generate access token failed", http.StatusInternalServerError, nil, err)
	}
	return signed, expiresAt, nil
}

func (m *TokenManager) ParseAccessToken(tokenText string) (*TokenClaims, error) {
	tokenText = strings.TrimSpace(tokenText)
	if tokenText == "" {
		return nil, httpx.NewAppError(httpx.CodeUnauthorized, "missing access token", http.StatusUnauthorized, nil, nil)
	}
	claims := &TokenClaims{}
	token, err := jwt.ParseWithClaims(tokenText, claims, func(token *jwt.Token) (any, error) {
		if token.Method != jwt.SigningMethodHS256 {
			return nil, fmt.Errorf("unexpected signing method %s", token.Header["alg"])
		}
		return m.secret, nil
	})
	if err != nil || token == nil || !token.Valid {
		return nil, httpx.NewAppError(httpx.CodeUnauthorized, "invalid access token", http.StatusUnauthorized, nil, err)
	}
	if claims.Subject == "" || claims.UserID == "" {
		return nil, httpx.NewAppError(httpx.CodeUnauthorized, "invalid access token claims", http.StatusUnauthorized, nil, nil)
	}
	return claims, nil
}
