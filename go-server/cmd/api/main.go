package main

import (
	"context"
	"errors"
	"flag"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/app"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"go.uber.org/zap"
)

func main() {
	configPath := flag.String("config", config.DefaultConfigPath, "YAML config path")
	flag.Parse()

	cfg, err := config.Load(*configPath)
	if err != nil {
		panic(err)
	}
	ctx := context.Background()
	application, err := app.New(ctx, cfg)
	if err != nil {
		panic(err)
	}
	defer func() {
		_ = application.Close(context.Background())
	}()

	server := &http.Server{
		Addr:         cfg.Server.Addr,
		Handler:      application.Router,
		ReadTimeout:  cfg.Server.ReadTimeout.Duration,
		WriteTimeout: cfg.Server.WriteTimeout.Duration,
		IdleTimeout:  cfg.Server.IdleTimeout.Duration,
	}

	serverErrors := make(chan error, 1)
	go func() {
		application.Logger.Info("api server starting", zap.String("addr", cfg.Server.Addr))
		serverErrors <- server.ListenAndServe()
	}()

	shutdown := make(chan os.Signal, 1)
	signal.Notify(shutdown, syscall.SIGINT, syscall.SIGTERM)

	select {
	case err := <-serverErrors:
		if !errors.Is(err, http.ErrServerClosed) {
			application.Logger.Fatal("api server failed", zap.Error(err))
		}
	case sig := <-shutdown:
		application.Logger.Info("api server shutting down", zap.String("signal", sig.String()))
		shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.Server.ShutdownTimeout.Duration)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			application.Logger.Error("graceful shutdown failed", zap.Error(err))
			_ = server.Close()
		}
	}
}
