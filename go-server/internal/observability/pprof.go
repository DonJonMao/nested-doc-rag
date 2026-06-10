package observability

import (
	"context"
	"net/http"
	"net/http/pprof"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"go.uber.org/zap"
)

func NewPprofServer(cfg config.ObservabilityConfig, logger *zap.Logger) *http.Server {
	if !cfg.PprofEnabled {
		return nil
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/debug/pprof/", pprof.Index)
	mux.HandleFunc("/debug/pprof/cmdline", pprof.Cmdline)
	mux.HandleFunc("/debug/pprof/profile", pprof.Profile)
	mux.HandleFunc("/debug/pprof/symbol", pprof.Symbol)
	mux.HandleFunc("/debug/pprof/trace", pprof.Trace)
	mux.Handle("/debug/pprof/allocs", pprof.Handler("allocs"))
	mux.Handle("/debug/pprof/block", pprof.Handler("block"))
	mux.Handle("/debug/pprof/goroutine", pprof.Handler("goroutine"))
	mux.Handle("/debug/pprof/heap", pprof.Handler("heap"))
	mux.Handle("/debug/pprof/mutex", pprof.Handler("mutex"))
	mux.Handle("/debug/pprof/threadcreate", pprof.Handler("threadcreate"))
	if logger != nil {
		logger.Info("pprof server configured", zap.String("addr", cfg.PprofAddr))
	}
	return &http.Server{
		Addr:    cfg.PprofAddr,
		Handler: mux,
	}
}

func ShutdownServer(ctx context.Context, server *http.Server) error {
	if server == nil {
		return nil
	}
	return server.Shutdown(ctx)
}
