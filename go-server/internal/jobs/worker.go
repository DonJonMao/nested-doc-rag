package jobs

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/hibiken/asynq"
	"go.uber.org/zap"
)

type Worker struct {
	server            *asynq.Server
	mux               *asynq.ServeMux
	repo              Repo
	service           *Service
	limiter           *ResourceLimiter
	logger            *zap.Logger
	namespace         string
	heartbeatInterval time.Duration
	handlers          map[string]TaskHandler
	mu                sync.RWMutex
}

type interruptedJobLister interface {
	ListInterrupted(ctx context.Context, staleBefore time.Time, limit int) ([]Job, error)
}

type interruptedJobHandler interface {
	RecoverInterruptedJob(ctx context.Context, job *Job, terminalStatus string, err error)
}

func NewWorker(redisCfg config.RedisConfig, jobsCfg config.JobsConfig, repo Repo, service *Service, limiter *ResourceLimiter, logger *zap.Logger) *Worker {
	if logger == nil {
		logger = zap.NewNop()
	}
	namespace := strings.TrimSpace(jobsCfg.RedisNamespace)
	if namespace == "" {
		namespace = "gongkan"
	}
	heartbeatInterval := jobsCfg.HeartbeatInterval.Duration
	if heartbeatInterval <= 0 {
		heartbeatInterval = 10 * time.Second
	}
	retryBackoff := jobsCfg.RetryBackoff.Duration
	if retryBackoff <= 0 {
		retryBackoff = 30 * time.Second
	}
	concurrency := jobsCfg.WorkerConcurrency
	if concurrency <= 0 {
		concurrency = 1
	}
	if limiter == nil {
		limiter = NewResourceLimiter(jobsCfg)
	}
	server := asynq.NewServer(
		asynq.RedisClientOpt{Addr: redisCfg.Addr, Password: redisCfg.Password, DB: redisCfg.DB},
		asynq.Config{
			Concurrency: concurrency,
			Queues: map[string]int{
				"high":    3,
				"default": 1,
			},
			RetryDelayFunc: func(n int, err error, task *asynq.Task) time.Duration {
				return retryBackoff
			},
			Logger:          asynqZapLogger{logger: logger},
			ShutdownTimeout: 30 * time.Second,
		},
	)
	worker := &Worker{
		server:            server,
		mux:               asynq.NewServeMux(),
		repo:              repo,
		service:           service,
		limiter:           limiter,
		logger:            logger,
		namespace:         namespace,
		heartbeatInterval: heartbeatInterval,
		handlers:          make(map[string]TaskHandler),
	}
	for _, jobType := range []string{JobTypeNoop, JobTypeIngestKnowledge, JobTypeFillForm, JobTypeArchiveArtifacts} {
		worker.mux.HandleFunc(TaskType(namespace, jobType), worker.processTask)
	}
	return worker
}

func (w *Worker) RegisterHandler(jobType string, handler TaskHandler) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.handlers[jobType] = handler
}

func (w *Worker) RegisterDefaultHandlers(events RunEventWriter) {
	w.RegisterHandler(JobTypeNoop, NewNoopHandler(events))
	w.RegisterHandler(JobTypeIngestKnowledge, NewPlaceholderHandler(JobTypeIngestKnowledge))
	w.RegisterHandler(JobTypeFillForm, NewPlaceholderHandler(JobTypeFillForm))
	w.RegisterHandler(JobTypeArchiveArtifacts, NewPlaceholderHandler(JobTypeArchiveArtifacts))
}

func (w *Worker) Run() error {
	w.recoverInterruptedBeforeStart(context.Background())
	return w.server.Run(w.mux)
}

func (w *Worker) Start() error {
	w.recoverInterruptedBeforeStart(context.Background())
	return w.server.Start(w.mux)
}

func (w *Worker) Stop() {
	w.server.Stop()
}

func (w *Worker) Shutdown() {
	w.server.Shutdown()
}

func (w *Worker) ProcessTask(ctx context.Context, task *asynq.Task) error {
	return w.processTask(ctx, task)
}

func (w *Worker) RecoverInterruptedJobs(ctx context.Context, staleAfter time.Duration) (int, error) {
	if w == nil || w.repo == nil || w.service == nil {
		return 0, nil
	}
	lister, ok := w.repo.(interruptedJobLister)
	if !ok {
		return 0, nil
	}
	if staleAfter <= 0 {
		staleAfter = w.heartbeatInterval * 3
	}
	if staleAfter <= 0 {
		staleAfter = 30 * time.Second
	}
	staleBefore := time.Now().UTC().Add(-staleAfter)
	candidates, err := lister.ListInterrupted(ctx, staleBefore, 500)
	if err != nil {
		return 0, err
	}
	recovered := 0
	var recoveryErrs []error
	for i := range candidates {
		job := candidates[i]
		var terminalStatus string
		var interruptedErr error
		var markErr error
		switch job.Status {
		case JobStatusCancelRequested:
			terminalStatus = JobStatusCanceled
			interruptedErr = ErrJobCanceled
			markErr = w.service.MarkCanceled(ctx, job)
		case JobStatusRunning:
			terminalStatus = JobStatusFailed
			interruptedErr = fmt.Errorf("worker interrupted while job was running; heartbeat stale before %s", staleBefore.Format(time.RFC3339))
			markErr = w.service.MarkFailed(ctx, job, interruptedErr.Error())
		default:
			continue
		}
		if markErr != nil {
			recoveryErrs = append(recoveryErrs, markErr)
			continue
		}
		w.recoverInterruptedResource(ctx, &job, terminalStatus, interruptedErr)
		recovered++
	}
	return recovered, errors.Join(recoveryErrs...)
}

func (w *Worker) recoverInterruptedBeforeStart(ctx context.Context) {
	recovered, err := w.RecoverInterruptedJobs(ctx, 0)
	if err != nil {
		w.logger.Warn("recover interrupted jobs failed", zap.Error(err))
		return
	}
	if recovered > 0 {
		w.logger.Info("recovered interrupted jobs", zap.Int("count", recovered))
	}
}

func (w *Worker) recoverInterruptedResource(ctx context.Context, job *Job, terminalStatus string, err error) {
	if job == nil {
		return
	}
	handler := w.handlerFor(job.JobType)
	recoverer, ok := handler.(interruptedJobHandler)
	if !ok {
		return
	}
	recoverer.RecoverInterruptedJob(ctx, job, terminalStatus, err)
}

func (w *Worker) processTask(ctx context.Context, task *asynq.Task) error {
	payload, err := DecodeTaskPayload(task.Payload())
	if err != nil || payload.JobID == uuid.Nil {
		return fmt.Errorf("%w: invalid task payload", asynq.SkipRetry)
	}
	job, err := w.repo.GetByID(ctx, payload.JobID)
	if err != nil {
		return err
	}
	switch job.Status {
	case JobStatusCanceled, JobStatusSucceeded:
		return nil
	case JobStatusCancelRequested:
		_ = w.service.MarkCanceled(ctx, *job)
		return nil
	case JobStatusFailed, JobStatusCompletedWithFailures:
		if !CanRetry(*job) {
			return fmt.Errorf("%w: job exhausted retries", asynq.SkipRetry)
		}
		now := time.Now().UTC()
		if err := w.repo.MarkQueued(ctx, job.ID, now); err != nil {
			return err
		}
		job.Status = JobStatusQueued
		job.QueuedAt = &now
		w.service.emit(ctx, *job, runevent.EventQueued, map[string]any{"retry": true})
	case JobStatusQueued:
	default:
		return fmt.Errorf("%w: job is not queued", asynq.SkipRetry)
	}
	if err := w.service.MarkRunning(ctx, *job); err != nil {
		return err
	}
	job.Attempt++
	job.Status = JobStatusRunning

	jobCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	heartbeatDone := make(chan struct{})
	go w.heartbeat(jobCtx, job, cancel, heartbeatDone)
	defer func() {
		cancel()
		<-heartbeatDone
	}()

	release, err := w.limiter.Acquire(jobCtx, job.JobType)
	if err != nil {
		_ = w.service.MarkFailed(ctx, *job, err.Error())
		return err
	}
	defer release()

	handler := w.handlerFor(job.JobType)
	err = handler.Handle(jobCtx, job)
	if jobCtx.Err() != nil || errors.Is(err, ErrJobCanceled) {
		if markErr := w.service.MarkCanceled(context.Background(), *job); markErr != nil {
			w.logger.Error("mark job canceled failed", zap.String("job_id", job.ID.String()), zap.Error(markErr))
		}
		return nil
	}
	if err != nil {
		failedJob := *job
		failedJob.Status = JobStatusFailed
		shouldRetry := !errors.Is(err, ErrHandlerNotImplemented) && CanRetry(failedJob)
		if markErr := w.service.MarkFailed(context.Background(), *job, err.Error()); markErr != nil {
			w.logger.Error("mark job failed failed", zap.String("job_id", job.ID.String()), zap.Error(markErr))
		}
		if !shouldRetry {
			return fmt.Errorf("%w: %v", asynq.SkipRetry, err)
		}
		return err
	}
	if err := w.service.MarkSucceeded(context.Background(), *job); err != nil {
		return err
	}
	return nil
}

func (w *Worker) heartbeat(ctx context.Context, job *Job, cancel context.CancelFunc, done chan<- struct{}) {
	defer close(done)
	interval := w.heartbeatInterval
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			current, err := w.repo.GetByID(ctx, job.ID)
			if err == nil && (current.Status == JobStatusCancelRequested || current.Status == JobStatusCanceled) {
				cancel()
				continue
			}
			if err := w.service.MarkHeartbeat(ctx, *job); err != nil {
				w.logger.Warn("job heartbeat failed", zap.String("job_id", job.ID.String()), zap.Error(err))
			}
		}
	}
}

func (w *Worker) handlerFor(jobType string) TaskHandler {
	w.mu.RLock()
	defer w.mu.RUnlock()
	if handler, ok := w.handlers[jobType]; ok && handler != nil {
		return handler
	}
	return NewPlaceholderHandler(jobType)
}

type asynqZapLogger struct {
	logger *zap.Logger
}

func (l asynqZapLogger) Debug(args ...interface{}) { l.logger.Sugar().Debug(args...) }
func (l asynqZapLogger) Info(args ...interface{})  { l.logger.Sugar().Info(args...) }
func (l asynqZapLogger) Warn(args ...interface{})  { l.logger.Sugar().Warn(args...) }
func (l asynqZapLogger) Error(args ...interface{}) { l.logger.Sugar().Error(args...) }
func (l asynqZapLogger) Fatal(args ...interface{}) { l.logger.Sugar().Fatal(args...) }
