package eventbus

import (
	"context"
	"encoding/json"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/redis/go-redis/v9"
	"go.uber.org/zap"
)

type RedisEventBus struct {
	client  *redis.Client
	channel string
	logger  *zap.Logger
}

func NewRedisEventBus(client *redis.Client, channel string, logger *zap.Logger) *RedisEventBus {
	if logger == nil {
		logger = zap.NewNop()
	}
	channel = strings.TrimSpace(channel)
	if channel == "" {
		channel = "gongkan:run_events"
	}
	return &RedisEventBus{client: client, channel: channel, logger: logger}
}

func (b *RedisEventBus) Publish(ctx context.Context, event runevent.RunEvent) error {
	payload, err := json.Marshal(event)
	if err != nil {
		return err
	}
	return b.client.Publish(ctx, b.channel, payload).Err()
}

func (b *RedisEventBus) Subscribe(ctx context.Context, handler func(runevent.RunEvent)) error {
	pubsub := b.client.Subscribe(ctx, b.channel)
	defer pubsub.Close()
	if _, err := pubsub.Receive(ctx); err != nil {
		if ctx.Err() != nil {
			return nil
		}
		return err
	}
	for {
		msg, err := pubsub.ReceiveMessage(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			b.logger.Error("receive run event bus message failed", zap.String("channel", b.channel), zap.Error(err))
			time.Sleep(time.Second)
			continue
		}
		var event runevent.RunEvent
		if err := json.Unmarshal([]byte(msg.Payload), &event); err != nil {
			b.logger.Error("decode run event bus message failed", zap.String("channel", b.channel), zap.Error(err))
			continue
		}
		handler(event)
	}
}

func (b *RedisEventBus) Close() error {
	return nil
}
