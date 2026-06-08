package httpx

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

type RouteDeps struct {
	ReadyChecks    []ReadyCheck
	MetricsHandler http.Handler
}

func RegisterRoutes(r chi.Router, deps RouteDeps) {
	r.Get("/healthz", Healthz)
	r.Get("/readyz", Readyz(deps.ReadyChecks))
	if deps.MetricsHandler != nil {
		r.Handle("/metrics", deps.MetricsHandler)
	} else {
		r.Handle("/metrics", promhttp.Handler())
	}
	r.Route("/api/v1", func(api chi.Router) {
		api.Get("/ping", func(w http.ResponseWriter, r *http.Request) {
			WriteOK(w, r, map[string]string{"service": "gongkan-platform", "version": "dev"})
		})
	})
}
