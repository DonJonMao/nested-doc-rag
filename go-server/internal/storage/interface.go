package storage

import (
	"context"
	"errors"
	"fmt"
	"io"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"go.uber.org/zap"
)

var (
	ErrInvalidKey   = errors.New("invalid object key")
	ErrNotSupported = errors.New("operation not supported")
)

type ObjectStorage interface {
	Put(ctx context.Context, key string, r io.Reader, size int64, contentType string) error
	Get(ctx context.Context, key string) (io.ReadCloser, ObjectInfo, error)
	Delete(ctx context.Context, key string) error
	PresignGet(ctx context.Context, key string, ttl time.Duration) (string, error)
	Health(ctx context.Context) error
}

type ObjectInfo struct {
	Key          string
	Size         int64
	ContentType  string
	ETag         string
	LastModified time.Time
}

func NewObjectStorage(cfg config.StorageConfig, logger *zap.Logger) (ObjectStorage, error) {
	switch cfg.Type {
	case "local":
		return NewLocalStorage(cfg.LocalDir)
	case "minio":
		return NewMinIOStorage(context.Background(), cfg.MinIO, logger)
	default:
		return nil, fmt.Errorf("unsupported storage type %q", cfg.Type)
	}
}
