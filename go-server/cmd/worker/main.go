package main

import (
	"context"
	"flag"
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
	application, err := app.New(context.Background(), cfg)
	if err != nil {
		panic(err)
	}
	defer func() {
		_ = application.Close(context.Background())
	}()

	application.Logger.Info("worker placeholder started")
	shutdown := make(chan os.Signal, 1)
	signal.Notify(shutdown, syscall.SIGINT, syscall.SIGTERM)
	sig := <-shutdown
	application.Logger.Info("worker placeholder shutting down", zap.String("signal", sig.String()))
}
