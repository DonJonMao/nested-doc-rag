package main

import (
	"context"
	"flag"
	"os"
	"os/signal"
	"syscall"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/database"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/eventbus"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/logging"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/redisx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/workspace"
	"go.uber.org/zap"
)

func main() {
	configPath := flag.String("config", config.DefaultConfigPath, "YAML config path")
	flag.Parse()

	cfg, err := config.Load(*configPath)
	if err != nil {
		panic(err)
	}
	logger, err := logging.New(cfg.Logging)
	if err != nil {
		panic(err)
	}
	defer func() {
		_ = logger.Sync()
	}()

	ctx := context.Background()
	db, err := database.NewPool(ctx, cfg.Database)
	if err != nil {
		logger.Fatal("connect database failed", zap.Error(err))
	}
	defer database.Close(db)

	if err := database.ApplyMigrations(ctx, db, "migrations"); err != nil {
		logger.Fatal("apply migrations failed", zap.Error(err))
	}
	redisClient, err := redisx.NewClient(cfg.Redis)
	if err != nil {
		logger.Fatal("connect redis failed", zap.Error(err))
	}
	defer func() {
		_ = redisx.Close(redisClient)
	}()
	var runEventBus eventbus.EventBus = eventbus.NewNoopEventBus()
	if cfg.Jobs.EventBusEnabled {
		runEventBus = eventbus.NewRedisEventBus(redisClient, cfg.Jobs.EventChannel, logger)
	}
	defer func() {
		_ = runEventBus.Close()
	}()

	auditRepo := audit.NewPGXRepo(db)
	auditService := audit.NewService(auditRepo, logger)
	workspaceRepo := workspace.NewPGXRepo(db)
	workspaceAuthorizer := workspace.NewAuthorizer(workspaceRepo)
	runEventRepo := runevent.NewPGXRepo(db)
	runEventService := runevent.NewService(runEventRepo, eventbus.NewRunEventPublisher(runEventBus, logger))
	jobRepo := jobs.NewPGXRepo(db)
	jobService := jobs.NewService(jobRepo, runEventService, nil, workspaceAuthorizer, auditService, logger, cfg.Jobs.MaxAttempts)
	limiter := jobs.NewResourceLimiter(cfg.Jobs)
	worker := jobs.NewWorker(cfg.Redis, cfg.Jobs, jobRepo, jobService, limiter, logger)
	worker.RegisterDefaultHandlers(runEventService)

	serverErrors := make(chan error, 1)
	go func() {
		logger.Info("worker starting", zap.Int("concurrency", cfg.Jobs.WorkerConcurrency), zap.String("redis_namespace", cfg.Jobs.RedisNamespace))
		serverErrors <- worker.Run()
	}()

	shutdown := make(chan os.Signal, 1)
	signal.Notify(shutdown, syscall.SIGINT, syscall.SIGTERM)

	select {
	case err := <-serverErrors:
		if err != nil {
			logger.Fatal("worker failed", zap.Error(err))
		}
	case sig := <-shutdown:
		logger.Info("worker shutting down", zap.String("signal", sig.String()))
		worker.Shutdown()
	}
}
