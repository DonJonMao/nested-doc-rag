package app

import (
	"context"
	"net/http"
	"os"

	artifactpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/database"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/eventbus"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	jobspkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/logging"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/observability"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/redisx"
	reviewpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/review"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	ssepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/sse"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
	userpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/user"
	workspacepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/workspace"
	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
	"go.uber.org/zap"
)

type App struct {
	Config   *config.Config
	Logger   *zap.Logger
	DB       *pgxpool.Pool
	Redis    *redis.Client
	Storage  storage.ObjectStorage
	Metrics  *observability.Metrics
	Router   http.Handler
	Queue    jobspkg.Queue
	Broker   *ssepkg.Broker
	EventBus eventbus.EventBus

	eventBusCancel context.CancelFunc
}

func New(ctx context.Context, cfg *config.Config) (*App, error) {
	logger, err := logging.New(cfg.Logging)
	if err != nil {
		return nil, err
	}
	db, err := database.NewPool(ctx, cfg.Database)
	if err != nil {
		_ = logger.Sync()
		return nil, err
	}
	redisClient, err := redisx.NewClient(cfg.Redis)
	if err != nil {
		database.Close(db)
		_ = logger.Sync()
		return nil, err
	}
	objectStorage, err := storage.NewObjectStorage(cfg.Storage, logger)
	if err != nil {
		_ = redisx.Close(redisClient)
		database.Close(db)
		_ = logger.Sync()
		return nil, err
	}
	if err := database.ApplyMigrations(ctx, db, "migrations"); err != nil {
		_ = redisx.Close(redisClient)
		database.Close(db)
		_ = logger.Sync()
		return nil, err
	}
	auditRepo := audit.NewPGXRepo(db)
	auditService := audit.NewService(auditRepo, logger)
	userRepo := userpkg.NewPGXRepo(db)
	refreshRepo := auth.NewPGXRefreshTokenRepo(db)
	workspaceRepo := workspacepkg.NewPGXRepo(db)
	jwtSecret := os.Getenv(cfg.Auth.JWTSecretEnv)
	tokenManager, err := auth.NewTokenManager(jwtSecret, cfg.Auth.AccessTokenTTL.Duration)
	if err != nil {
		_ = redisx.Close(redisClient)
		database.Close(db)
		_ = logger.Sync()
		return nil, err
	}
	authService := auth.NewService(userRepo, userRepo, refreshRepo, tokenManager, cfg.Auth.RefreshTokenTTL.Duration, auditService)
	if err := authService.EnsureDefaultRoles(ctx); err != nil {
		_ = redisx.Close(redisClient)
		database.Close(db)
		_ = logger.Sync()
		return nil, err
	}
	if cfg.Auth.BootstrapAdmin.Enabled {
		password := os.Getenv(cfg.Auth.BootstrapAdmin.PasswordEnv)
		if password == "" {
			logger.Warn("bootstrap admin password env is not set", zap.String("password_env", cfg.Auth.BootstrapAdmin.PasswordEnv))
		} else if err := authService.BootstrapAdmin(ctx, cfg.Auth.BootstrapAdmin.Username, password); err != nil {
			_ = redisx.Close(redisClient)
			database.Close(db)
			_ = logger.Sync()
			return nil, err
		}
	}
	userService := userpkg.NewService(userRepo, auditService)
	workspaceService := workspacepkg.NewService(workspaceRepo, auditService)
	workspaceAuthorizer := workspacepkg.NewAuthorizer(workspaceRepo)
	fileRepo := filepkg.NewPGXRepo(db)
	fileValidator := filepkg.NewValidator(cfg.Files.MaxUploadSize.Bytes, cfg.Files.AllowedExtensions, cfg.Files.AllowedMIMETypes)
	fileService := filepkg.NewService(fileRepo, objectStorage, workspaceAuthorizer, auditService, fileValidator, cfg.Files.TempDir, cfg.Files.DeleteObjectOnSoftDelete)
	artifactRepo := artifactpkg.NewPGXRepo(db)
	artifactService := artifactpkg.NewService(
		artifactRepo,
		objectStorage,
		workspaceAuthorizer,
		auditService,
		artifactpkg.ServiceOptions{
			DownloadMode:         cfg.Artifacts.DownloadMode,
			AllowPresignDownload: cfg.Artifacts.AllowPresignDownload,
			DefaultPresignTTL:    cfg.Artifacts.DefaultPresignTTL.Duration,
		},
	)
	sseBroker := ssepkg.NewBroker(cfg.Jobs.EventBufferSize)
	runEventBus := newEventBus(redisClient, cfg, logger)
	eventBusCtx, eventBusCancel := context.WithCancel(context.Background())
	go func() {
		if err := runEventBus.Subscribe(eventBusCtx, func(event runevent.RunEvent) {
			sseBroker.PublishRunEvent(event)
		}); err != nil {
			logger.Error("run event bus subscription stopped", zap.Error(err))
		}
	}()
	runEventRepo := runevent.NewPGXRepo(db)
	runEventService := runevent.NewService(runEventRepo, sseBroker)
	jobRepo := jobspkg.NewPGXRepo(db)
	jobQueue := jobspkg.NewAsynqQueue(cfg.Redis, cfg.Jobs)
	metrics := observability.NewMetrics(cfg.Observability.MetricsEnabled)
	jobService := jobspkg.NewService(jobRepo, runEventService, jobQueue, workspaceAuthorizer, auditService, logger, cfg.Jobs.MaxAttempts, metrics)
	formFileRepo := formpkg.NewPGXFormFileRepo(db)
	fillRunRepo := formpkg.NewPGXFillRunRepo(db)
	formFileService := formpkg.NewFormFileService(formFileRepo, fileService, workspaceAuthorizer, auditService, logger)
	fillRunService := formpkg.NewFillRunService(fillRunRepo, formFileRepo, jobService, artifactService, workspaceAuthorizer, auditService, logger, *cfg)
	reviewRepo := reviewpkg.NewPGXRepo(db)
	reviewService := reviewpkg.NewService(reviewRepo, fillRunRepo, workspaceAuthorizer, auditService, logger, metrics)
	knowledgeBaseRepo := knowledgepkg.NewPGXKnowledgeBaseRepo(db)
	knowledgeDocumentRepo := knowledgepkg.NewPGXKnowledgeDocumentRepo(db)
	knowledgeIndexVersionRepo := knowledgepkg.NewPGXKnowledgeIndexVersionRepo(db)
	ingestionJobRepo := knowledgepkg.NewPGXIngestionJobRepo(db)
	knowledgeBaseService := knowledgepkg.NewKnowledgeBaseService(knowledgeBaseRepo, knowledgeIndexVersionRepo, workspaceAuthorizer, auditService, logger)
	knowledgeDocumentService := knowledgepkg.NewKnowledgeDocumentService(knowledgeBaseRepo, knowledgeDocumentRepo, fileService, workspaceAuthorizer, auditService, logger)
	ingestionService := knowledgepkg.NewIngestionService(knowledgeBaseRepo, knowledgeDocumentRepo, knowledgeIndexVersionRepo, ingestionJobRepo, jobService, workspaceAuthorizer, auditService, logger, *cfg)
	routes := platformRoutes{
		tokenManager:     tokenManager,
		authHandler:      auth.NewHandler(authService),
		userHandler:      userpkg.NewHandler(userService),
		workspaceHandler: workspacepkg.NewHandler(workspaceService),
		fileHandler:      filepkg.NewHandler(fileService),
		artifactHandler:  artifactpkg.NewHandler(artifactService),
		jobsHandler:      jobspkg.NewHandler(jobService, cfg.Jobs.EnableNoopJob),
		sseHandler:       ssepkg.NewHandler(runEventService, sseBroker, workspaceAuthorizer, metrics),
		formHandler:      formpkg.NewHandler(formFileService, fillRunService),
		knowledgeHandler: knowledgepkg.NewHandler(knowledgeBaseService, knowledgeDocumentService, ingestionService),
		reviewHandler:    reviewpkg.NewHandler(reviewService, fillRunService),
		enableNoopJob:    cfg.Jobs.EnableNoopJob,
	}
	router := buildRouter(cfg, logger, db, redisClient, objectStorage, metrics, routes)
	return &App{
		Config:         cfg,
		Logger:         logger,
		DB:             db,
		Redis:          redisClient,
		Storage:        objectStorage,
		Metrics:        metrics,
		Router:         router,
		Queue:          jobQueue,
		Broker:         sseBroker,
		EventBus:       runEventBus,
		eventBusCancel: eventBusCancel,
	}, nil
}

func (a *App) Close(ctx context.Context) error {
	_ = ctx
	if a.eventBusCancel != nil {
		a.eventBusCancel()
	}
	if a.EventBus != nil {
		_ = a.EventBus.Close()
	}
	if a.Broker != nil {
		a.Broker.Close()
	}
	if a.Queue != nil {
		_ = a.Queue.Close()
	}
	if a.Redis != nil {
		_ = redisx.Close(a.Redis)
	}
	if a.DB != nil {
		database.Close(a.DB)
	}
	if a.Logger != nil {
		_ = a.Logger.Sync()
	}
	return nil
}

func newEventBus(redisClient *redis.Client, cfg *config.Config, logger *zap.Logger) eventbus.EventBus {
	if cfg == nil || !cfg.Jobs.EventBusEnabled {
		return eventbus.NewNoopEventBus()
	}
	return eventbus.NewRedisEventBus(redisClient, cfg.Jobs.EventChannel, logger)
}

func buildRouter(
	cfg *config.Config,
	logger *zap.Logger,
	db *pgxpool.Pool,
	redisClient *redis.Client,
	objectStorage storage.ObjectStorage,
	metrics *observability.Metrics,
	routes platformRoutes,
) http.Handler {
	r := chi.NewRouter()
	tracingProvider := observability.NewTracerProvider(cfg.Observability, logger)
	r.Use(middleware.RequestID)
	r.Use(middleware.Recover(logger))
	r.Use(middleware.SecurityHeaders(cfg.Security))
	r.Use(middleware.Logger(logger))
	r.Use(metrics.HTTPMiddleware)
	r.Use(tracingProvider.Middleware)
	r.Use(middleware.BodyLimit(cfg.Security))
	r.Use(middleware.RateLimit(cfg.Security))
	r.Use(middleware.Timeout(cfg.Server.WriteTimeout.Duration))
	corsCfg := cfg.CORS
	corsCfg.AllowCredentials = corsCfg.AllowCredentials || cfg.Security.CORSAllowCredentials
	r.Use(middleware.CORS(corsCfg))
	httpx.RegisterRoutes(r, httpx.RouteDeps{
		MetricsHandler: metrics.Handler(),
		ReadyChecks: []httpx.ReadyCheck{
			{
				Name: "database",
				Check: func(ctx context.Context) error {
					err := database.Ping(ctx, db)
					metrics.ObserveReadyCheck("database", err == nil)
					return err
				},
			},
			{
				Name: "redis",
				Check: func(ctx context.Context) error {
					err := redisx.Ping(ctx, redisClient)
					metrics.ObserveReadyCheck("redis", err == nil)
					return err
				},
			},
			{
				Name: "storage",
				Check: func(ctx context.Context) error {
					err := objectStorage.Health(ctx)
					metrics.ObserveReadyCheck("storage", err == nil)
					return err
				},
			},
		},
	})
	registerPlatformRoutes(r, routes)
	return r
}

type platformRoutes struct {
	tokenManager     *auth.TokenManager
	authHandler      *auth.Handler
	userHandler      *userpkg.Handler
	workspaceHandler *workspacepkg.Handler
	fileHandler      *filepkg.Handler
	artifactHandler  *artifactpkg.Handler
	jobsHandler      *jobspkg.Handler
	sseHandler       *ssepkg.Handler
	formHandler      *formpkg.Handler
	knowledgeHandler *knowledgepkg.Handler
	reviewHandler    *reviewpkg.Handler
	enableNoopJob    bool
}

func registerPlatformRoutes(r chi.Router, routes platformRoutes) {
	if routes.authHandler == nil || routes.tokenManager == nil {
		return
	}
	r.Route("/api/v1", func(api chi.Router) {
		routes.authHandler.RegisterPublicRoutes(api)
		api.Group(func(protected chi.Router) {
			protected.Use(middleware.Auth(routes.tokenManager))
			routes.authHandler.RegisterProtectedRoutes(protected)
			if routes.workspaceHandler != nil {
				routes.workspaceHandler.RegisterRoutes(protected)
			}
			if routes.fileHandler != nil {
				routes.fileHandler.RegisterRoutes(protected)
			}
			if routes.artifactHandler != nil {
				routes.artifactHandler.RegisterRoutes(protected)
			}
			if routes.jobsHandler != nil {
				routes.jobsHandler.RegisterRoutes(protected)
			}
			if routes.sseHandler != nil {
				routes.sseHandler.RegisterRoutes(protected)
			}
			if routes.formHandler != nil {
				routes.formHandler.RegisterRoutes(protected)
			}
			if routes.knowledgeHandler != nil {
				routes.knowledgeHandler.RegisterRoutes(protected)
			}
			if routes.reviewHandler != nil {
				routes.reviewHandler.RegisterRoutes(protected)
			}
			if routes.userHandler != nil {
				protected.Group(func(admin chi.Router) {
					admin.Use(middleware.RequireRoles(auth.RoleAdmin))
					routes.userHandler.RegisterRoutes(admin)
					if routes.jobsHandler != nil && routes.enableNoopJob {
						routes.jobsHandler.RegisterAdminRoutes(admin)
					}
				})
			}
		})
	})
}
