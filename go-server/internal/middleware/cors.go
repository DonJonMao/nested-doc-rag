package middleware

import (
	"net/http"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
)

func CORS(cfg config.CORSConfig) func(http.Handler) http.Handler {
	origins := toSet(cfg.AllowedOrigins)
	methods := strings.Join(cfg.AllowedMethods, ",")
	headers := strings.Join(cfg.AllowedHeaders, ",")
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			if origin != "" && (origins["*"] || origins[origin]) {
				w.Header().Set("Access-Control-Allow-Origin", origin)
				w.Header().Set("Access-Control-Allow-Methods", methods)
				w.Header().Set("Access-Control-Allow-Headers", headers)
				w.Header().Set("Access-Control-Allow-Credentials", "true")
			}
			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func toSet(values []string) map[string]bool {
	output := make(map[string]bool, len(values))
	for _, value := range values {
		if value != "" {
			output[value] = true
		}
	}
	return output
}
