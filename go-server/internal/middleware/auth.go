package middleware

import (
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
)

func Auth(tokens *auth.TokenManager) func(http.Handler) http.Handler {
	return auth.AuthMiddleware(tokens)
}
