package auth

import (
	"net/http"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"golang.org/x/crypto/bcrypt"
)

const minPasswordLength = 8

func HashPassword(password string) (string, error) {
	if err := ValidatePasswordStrength(password); err != nil {
		return "", err
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return "", httpx.NewAppError(httpx.CodeInternal, "hash password failed", http.StatusInternalServerError, nil, err)
	}
	return string(hash), nil
}

func VerifyPassword(hash string, password string) bool {
	if strings.TrimSpace(hash) == "" || password == "" {
		return false
	}
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(password)) == nil
}

func ValidatePasswordStrength(password string) error {
	if strings.TrimSpace(password) == "" {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "password is required", http.StatusBadRequest, nil, nil)
	}
	if len(password) < minPasswordLength {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "password must be at least 8 characters", http.StatusBadRequest, nil, nil)
	}
	return nil
}
