package app

import (
	"context"
	"net/http"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/database"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/logging"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/middleware"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/observability"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/redisx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
	"go.uber.org/zap"
)

type App struct {
	Config  *config.Config
	Logger  *zap.Logger
	DB      *pgxpool.Pool
	Redis   *redis.Client
	Storage storage.ObjectStorage
	Metrics *observability.Metrics
	Router  http.Handler
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
	metrics := observability.NewMetrics()
	router := buildRouter(cfg, logger, db, redisClient, objectStorage, metrics)
	return &App{
		Config:  cfg,
		Logger:  logger,
		DB:      db,
		Redis:   redisClient,
		Storage: objectStorage,
		Metrics: metrics,
		Router:  router,
	}, nil
}

func (a *App) Close(ctx context.Context) error {
	_ = ctx
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

func buildRouter(
	cfg *config.Config,
	logger *zap.Logger,
	db *pgxpool.Pool,
	redisClient *redis.Client,
	objectStorage storage.ObjectStorage,
	metrics *observability.Metrics,
) http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middleware.Recover(logger))
	r.Use(middleware.Logger(logger))
	r.Use(middleware.Timeout(cfg.Server.WriteTimeout.Duration))
	r.Use(middleware.CORS(cfg.CORS))
	r.Use(middleware.RateLimit())
	r.Use(metrics.HTTPMiddleware)
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
	return r
}
