package database

import (
	"context"
	"fmt"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/jackc/pgx/v5/pgxpool"
)

func NewPool(ctx context.Context, cfg config.DatabaseConfig) (*pgxpool.Pool, error) {
	poolCfg, err := pgxpool.ParseConfig(cfg.DSN)
	if err != nil {
		return nil, fmt.Errorf("parse postgres dsn: %w", err)
	}
	if cfg.MaxOpenConns > 0 {
		poolCfg.MaxConns = cfg.MaxOpenConns
	}
	if cfg.MaxIdleConns > 0 {
		poolCfg.MinConns = minInt32(cfg.MaxIdleConns, poolCfg.MaxConns)
	}
	if cfg.MaxConnLifetime.Duration > 0 {
		poolCfg.MaxConnLifetime = cfg.MaxConnLifetime.Duration
	}
	pool, err := pgxpool.NewWithConfig(ctx, poolCfg)
	if err != nil {
		return nil, fmt.Errorf("create postgres pool: %w", err)
	}
	return pool, nil
}

func Ping(ctx context.Context, pool *pgxpool.Pool) error {
	if pool == nil {
		return fmt.Errorf("postgres pool is nil")
	}
	return pool.Ping(ctx)
}

func Close(pool *pgxpool.Pool) {
	if pool != nil {
		pool.Close()
	}
}

func minInt32(a int32, b int32) int32 {
	if a < b {
		return a
	}
	return b
}
