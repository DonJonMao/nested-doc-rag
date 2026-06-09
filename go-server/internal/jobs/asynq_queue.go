package jobs

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/hibiken/asynq"
)

type AsynqQueue struct {
	client     *asynq.Client
	namespace  string
	timeout    time.Duration
	maxAttempt int
}

func NewAsynqQueue(redisCfg config.RedisConfig, jobsCfg config.JobsConfig) *AsynqQueue {
	return &AsynqQueue{
		client: asynq.NewClient(asynq.RedisClientOpt{
			Addr:     redisCfg.Addr,
			Password: redisCfg.Password,
			DB:       redisCfg.DB,
		}),
		namespace:  strings.TrimSpace(jobsCfg.RedisNamespace),
		timeout:    jobsCfg.DefaultTimeout.Duration,
		maxAttempt: jobsCfg.MaxAttempts,
	}
}

func (q *AsynqQueue) Enqueue(ctx context.Context, job Job) error {
	payload, err := EncodeTaskPayload(job.ID)
	if err != nil {
		return err
	}
	maxRetry := job.MaxAttempts - 1
	if maxRetry < 0 {
		maxRetry = q.maxAttempt - 1
	}
	if maxRetry < 0 {
		maxRetry = 0
	}
	timeout := q.timeout
	if timeout <= 0 {
		timeout = 2 * time.Hour
	}
	_, err = q.client.EnqueueContext(
		ctx,
		asynq.NewTask(TaskType(q.namespace, job.JobType), payload),
		asynq.MaxRetry(maxRetry),
		asynq.Timeout(timeout),
		asynq.Queue(queueName(job)),
	)
	return err
}

func (q *AsynqQueue) Close() error {
	return q.client.Close()
}

func TaskType(namespace string, jobType string) string {
	namespace = strings.TrimSpace(namespace)
	if namespace == "" {
		namespace = "gongkan"
	}
	return fmt.Sprintf("%s:%s", namespace, jobType)
}

func queueName(job Job) string {
	if job.Priority > 0 {
		return "high"
	}
	return "default"
}
