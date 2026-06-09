package main

import (
	"context"
	"flag"
	"os"
	"os/signal"
	"syscall"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/database"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/eventbus"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/logging"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/redisx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
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
	objectStorage, err := storage.NewObjectStorage(cfg.Storage, logger)
	if err != nil {
		logger.Fatal("initialize object storage failed", zap.Error(err))
	}
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
	artifactRepo := artifact.NewPGXRepo(db)
	artifactService := artifact.NewService(
		artifactRepo,
		objectStorage,
		workspaceAuthorizer,
		auditService,
		artifact.ServiceOptions{
			DownloadMode:         cfg.Artifacts.DownloadMode,
			AllowPresignDownload: cfg.Artifacts.AllowPresignDownload,
			DefaultPresignTTL:    cfg.Artifacts.DefaultPresignTTL.Duration,
		},
	)
	runEventRepo := runevent.NewPGXRepo(db)
	runEventService := runevent.NewService(runEventRepo, eventbus.NewRunEventPublisher(runEventBus, logger))
	jobRepo := jobs.NewPGXRepo(db)
	jobService := jobs.NewService(jobRepo, runEventService, nil, workspaceAuthorizer, auditService, logger, cfg.Jobs.MaxAttempts)
	limiter := jobs.NewResourceLimiter(cfg.Jobs)
	worker := jobs.NewWorker(cfg.Redis, cfg.Jobs, jobRepo, jobService, limiter, logger)
	commandBuilder := &pythonpkg.CommandBuilder{
		PythonExecutable:  cfg.Python.Executable,
		ProjectDir:        cfg.Python.ProjectDir,
		DefaultConfigPath: cfg.Python.ConfigPath,
	}
	processRunner := &pythonpkg.ProcessRunner{
		Logger:          logger,
		KillGracePeriod: cfg.Python.KillGracePeriod.Duration,
		StdoutLimit:     cfg.Python.StdoutLogMaxBytes,
		StderrLimit:     cfg.Python.StderrLogMaxBytes,
	}
	pythonRunner := &pythonpkg.SubprocessPythonRunner{
		Builder:                    commandBuilder,
		Process:                    processRunner,
		ArtifactValidationEnabled:  cfg.Python.ArtifactValidationEnabled,
		DefaultTimeout:             cfg.Python.DefaultTimeout.Duration,
		Step15DefaultRetrievalMode: cfg.Python.Step15DefaultRetrievalMode,
		Step15DefaultPromptVersion: cfg.Python.Step15DefaultPromptVersion,
		Step15DefaultRows:          cfg.Python.Step15DefaultRows,
		IngestCommandEnabled:       cfg.Python.IngestCommandEnabled,
	}
	logger.Info("python runner configured",
		zap.String("python_executable", cfg.Python.Executable),
		zap.String("python_project_dir", cfg.Python.ProjectDir),
		zap.Bool("artifact_validation_enabled", cfg.Python.ArtifactValidationEnabled),
		zap.String("step15_default_retrieval_mode", cfg.Python.Step15DefaultRetrievalMode),
		zap.String("step15_default_prompt_version", cfg.Python.Step15DefaultPromptVersion),
	)
	artifactArchiver := pythonpkg.NewArtifactArchiver(artifactService, logger)
	worker.RegisterHandler(jobs.JobTypeNoop, jobs.NewNoopHandler(runEventService))
	worker.RegisterHandler(jobs.JobTypeFillForm, jobs.NewFillFormPythonHandler(pythonRunner, artifactArchiver, runEventService, logger))
	worker.RegisterHandler(jobs.JobTypeIngestKnowledge, jobs.NewIngestKnowledgePythonHandler(pythonRunner, runEventService, logger, cfg.Python.IngestCommandEnabled))
	worker.RegisterHandler(jobs.JobTypeArchiveArtifacts, jobs.NewPlaceholderHandler(jobs.JobTypeArchiveArtifacts))

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
