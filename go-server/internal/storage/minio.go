package storage

import (
	"context"
	"fmt"
	"io"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
	"go.uber.org/zap"
)

type MinIOStorage struct {
	client *minio.Client
	bucket string
}

func NewMinIOStorage(ctx context.Context, cfg config.MinIOConfig, logger *zap.Logger) (*MinIOStorage, error) {
	client, err := minio.New(cfg.Endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(cfg.AccessKey, cfg.SecretKey, ""),
		Secure: cfg.UseSSL,
	})
	if err != nil {
		return nil, fmt.Errorf("create minio client: %w", err)
	}
	exists, err := client.BucketExists(ctx, cfg.Bucket)
	if err != nil {
		return nil, fmt.Errorf("check minio bucket: %w", err)
	}
	if !exists {
		if logger != nil {
			logger.Info("creating minio bucket", zap.String("bucket", cfg.Bucket), zap.String("endpoint", cfg.Endpoint))
		}
		if err := client.MakeBucket(ctx, cfg.Bucket, minio.MakeBucketOptions{}); err != nil {
			return nil, fmt.Errorf("create minio bucket: %w", err)
		}
	}
	return &MinIOStorage{client: client, bucket: cfg.Bucket}, nil
}

func (s *MinIOStorage) Put(ctx context.Context, key string, r io.Reader, size int64, contentType string) error {
	cleanKey, err := cleanObjectKey(key)
	if err != nil {
		return err
	}
	_, err = s.client.PutObject(ctx, s.bucket, cleanKey, r, size, minio.PutObjectOptions{ContentType: contentType})
	return err
}

func (s *MinIOStorage) Get(ctx context.Context, key string) (io.ReadCloser, ObjectInfo, error) {
	cleanKey, err := cleanObjectKey(key)
	if err != nil {
		return nil, ObjectInfo{}, err
	}
	object, err := s.client.GetObject(ctx, s.bucket, cleanKey, minio.GetObjectOptions{})
	if err != nil {
		return nil, ObjectInfo{}, err
	}
	stat, err := object.Stat()
	if err != nil {
		_ = object.Close()
		return nil, ObjectInfo{}, err
	}
	return object, ObjectInfo{
		Key:          cleanKey,
		Size:         stat.Size,
		ContentType:  stat.ContentType,
		ETag:         stat.ETag,
		LastModified: stat.LastModified,
	}, nil
}

func (s *MinIOStorage) Delete(ctx context.Context, key string) error {
	cleanKey, err := cleanObjectKey(key)
	if err != nil {
		return err
	}
	return s.client.RemoveObject(ctx, s.bucket, cleanKey, minio.RemoveObjectOptions{})
}

func (s *MinIOStorage) PresignGet(ctx context.Context, key string, ttl time.Duration) (string, error) {
	cleanKey, err := cleanObjectKey(key)
	if err != nil {
		return "", err
	}
	u, err := s.client.PresignedGetObject(ctx, s.bucket, cleanKey, ttl, nil)
	if err != nil {
		return "", err
	}
	return u.String(), nil
}

func (s *MinIOStorage) Health(ctx context.Context) error {
	exists, err := s.client.BucketExists(ctx, s.bucket)
	if err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("minio bucket %q does not exist", s.bucket)
	}
	return nil
}
