package httpx

import (
	"context"
	"net/http"
	"time"
)

type ReadyCheck struct {
	Name  string
	Check func(ctx context.Context) error
}

func Healthz(w http.ResponseWriter, r *http.Request) {
	WriteOK(w, r, map[string]string{"status": "ok"})
}

func Readyz(checks []ReadyCheck) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()
		status := "ok"
		results := make(map[string]string, len(checks))
		failures := make(map[string]string)
		for _, check := range checks {
			if check.Name == "" || check.Check == nil {
				continue
			}
			if err := check.Check(ctx); err != nil {
				status = "degraded"
				results[check.Name] = "failed"
				failures[check.Name] = err.Error()
			} else {
				results[check.Name] = "ok"
			}
		}
		data := map[string]any{"status": status, "checks": results}
		if len(failures) > 0 {
			WriteError(w, r, NewAppError(CodeInternal, "service is not ready", http.StatusServiceUnavailable, data, nil))
			return
		}
		WriteOK(w, r, data)
	}
}
