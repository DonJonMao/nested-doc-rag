package middleware

import (
	"net/http"
	"strconv"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
)

func SecurityHeaders(cfg config.SecurityConfig) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		if !cfg.SecurityHeadersEnabled {
			return next
		}
		hstsMaxAge := int(cfg.HSTSMaxAge.Duration.Seconds())
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			header := w.Header()
			header.Set("X-Content-Type-Options", "nosniff")
			header.Set("X-Frame-Options", "DENY")
			header.Set("X-XSS-Protection", "0")
			header.Set("Referrer-Policy", "no-referrer")
			header.Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
			header.Set("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
			if cfg.HSTSEnabled && hstsMaxAge > 0 {
				header.Set("Strict-Transport-Security", "max-age="+strconv.Itoa(hstsMaxAge)+"; includeSubDomains")
			}
			next.ServeHTTP(w, r)
		})
	}
}
