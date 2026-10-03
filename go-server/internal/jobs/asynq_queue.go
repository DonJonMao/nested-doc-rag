package jobs

import (
	"context"
	"errors"
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

const asynqPracticalNoTimeout = 100 * 365 * 24 * time.Hour

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
	_, err = q.client.EnqueueContext(
		ctx,
		asynq.NewTask(TaskType(q.namespace, job.JobType), payload),
		q.enqueueOptions(job, maxRetry)...,
	)
	if errors.Is(err, asynq.ErrTaskIDConflict) {
		return nil
	}
	return err
}

func (q *AsynqQueue) enqueueOptions(job Job, maxRetry int) []asynq.Option {
	timeout := q.timeout
	if timeout <= 0 {
		// Asynq v0.25.1 falls back to its 30m default when timeout is zero
		// or omitted. Use a large explicit duration to preserve our "0s means
		// no practical job timeout" configuration.
		timeout = asynqPracticalNoTimeout
	}
	return []asynq.Option{
		asynq.TaskID(job.ID.String()),
		asynq.MaxRetry(maxRetry),
		asynq.Queue(queueName(job)),
		asynq.Timeout(timeout),
	}
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
