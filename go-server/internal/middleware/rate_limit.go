package middleware

import "net/http"

func RateLimit() func(http.Handler) http.Handler {
	// TODO(Block 8): replace this no-op with configurable IP/workspace limits.
	return func(next http.Handler) http.Handler {
		return next
	}
}
