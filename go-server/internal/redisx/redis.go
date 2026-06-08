package redisx

import (
	"context"
	"fmt"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/redis/go-redis/v9"
)

func NewClient(cfg config.RedisConfig) (*redis.Client, error) {
	if cfg.Addr == "" {
		return nil, fmt.Errorf("redis addr is required")
	}
	return redis.NewClient(&redis.Options{Addr: cfg.Addr, Password: cfg.Password, DB: cfg.DB}), nil
}

func Ping(ctx context.Context, client *redis.Client) error {
	if client == nil {
		return fmt.Errorf("redis client is nil")
	}
	return client.Ping(ctx).Err()
}

func Close(client *redis.Client) error {
	if client == nil {
		return nil
	}
	return client.Close()
}
