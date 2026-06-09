package jobs

import (
	"context"
	"net/http"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type WorkspaceAuthorizer interface {
	CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
	CanWriteWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
}

type RunEventWriter interface {
	Create(ctx context.Context, event runevent.RunEvent) (*runevent.RunEvent, error)
}

type Service struct {
	repo        Repo
	events      RunEventWriter
	queue       Queue
	authorizer  WorkspaceAuthorizer
	audit       *audit.Service
	logger      *zap.Logger
	maxAttempts int
}

func NewService(repo Repo, events RunEventWriter, queue Queue, authorizer WorkspaceAuthorizer, auditSvc *audit.Service, logger *zap.Logger, maxAttempts int) *Service {
	if logger == nil {
		logger = zap.NewNop()
	}
	if maxAttempts <= 0 {
		maxAttempts = 3
	}
	return &Service{repo: repo, events: events, queue: queue, authorizer: authorizer, audit: auditSvc, logger: logger, maxAttempts: maxAttempts}
}

func (s *Service) CreateJob(ctx context.Context, req CreateJobRequest, actor auth.Principal) (*Job, error) {
	if err := s.authorizer.CanWriteWorkspace(ctx, req.WorkspaceID, actor); err != nil {
		return nil, err
	}
	if !ValidJobType(req.JobType) {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid job type", http.StatusBadRequest, map[string]string{"job_type": req.JobType}, nil)
	}
	if !ValidResourceType(req.ResourceType) {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid resource type", http.StatusBadRequest, map[string]string{"resource_type": req.ResourceType}, nil)
	}
	jobID := uuid.New()
	resourceID := req.ResourceID
	if resourceID == uuid.Nil {
		resourceID = jobID
	}
	maxAttempts := req.MaxAttempts
	if maxAttempts <= 0 {
		maxAttempts = s.maxAttempts
	}
	job := Job{
		ID:           jobID,
		WorkspaceID:  req.WorkspaceID,
		JobType:      req.JobType,
		ResourceType: req.ResourceType,
		ResourceID:   resourceID,
		Status:       JobStatusCreated,
		Priority:     req.Priority,
		MaxAttempts:  maxAttempts,
		Payload:      req.Payload,
		CreatedBy:    actor.UserID,
		CreatedAt:    time.Now().UTC(),
		UpdatedAt:    time.Now().UTC(),
	}
	if job.Payload == nil {
		job.Payload = map[string]any{}
	}
	if err := s.repo.Create(ctx, job); err != nil {
		return nil, err
	}
	s.record(ctx, audit.AuditLog{WorkspaceID: &job.WorkspaceID, UserID: &actor.UserID, Action: "job.created", ResourceType: "job", ResourceID: job.ID.String(), Payload: map[string]any{"job_type": job.JobType, "resource_type": job.ResourceType}})
	if err := s.EnqueueJob(ctx, job.ID, actor); err != nil {
		return nil, err
	}
	return s.repo.GetByID(ctx, job.ID)
}

func (s *Service) EnqueueJob(ctx context.Context, jobID uuid.UUID, actor auth.Principal) error {
	job, err := s.repo.GetByID(ctx, jobID)
	if err != nil {
		return err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, job.WorkspaceID, actor); err != nil {
		return err
	}
	if job.Status != JobStatusCreated && job.Status != JobStatusFailed && job.Status != JobStatusCompletedWithFailures {
		return httpx.NewAppError(httpx.CodeConflict, "job cannot be queued from current status", http.StatusConflict, map[string]string{"status": job.Status}, nil)
	}
	now := time.Now().UTC()
	if err := s.repo.MarkQueued(ctx, job.ID, now); err != nil {
		return err
	}
	job.Status = JobStatusQueued
	job.QueuedAt = &now
	if s.queue != nil {
		if err := s.queue.Enqueue(ctx, *job); err != nil {
			return httpx.NewAppError(httpx.CodeInternal, "enqueue job failed", http.StatusInternalServerError, nil, err)
		}
	}
	s.emit(ctx, *job, runevent.EventQueued, map[string]any{"job_type": job.JobType})
	s.record(ctx, audit.AuditLog{WorkspaceID: &job.WorkspaceID, UserID: &actor.UserID, Action: "job.queued", ResourceType: "job", ResourceID: job.ID.String(), Payload: map[string]any{"job_type": job.JobType}})
	return nil
}

func (s *Service) GetJob(ctx context.Context, jobID uuid.UUID, actor auth.Principal) (*Job, error) {
	job, err := s.repo.GetByID(ctx, jobID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, job.WorkspaceID, actor); err != nil {
		return nil, err
	}
	return job, nil
}

func (s *Service) ListJobs(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int, actor auth.Principal) ([]Job, error) {
	if err := s.authorizer.CanReadWorkspace(ctx, workspaceID, actor); err != nil {
		return nil, err
	}
	return s.repo.ListByWorkspace(ctx, workspaceID, status, limit, offset)
}

func (s *Service) CancelJob(ctx context.Context, jobID uuid.UUID, actor auth.Principal) (*Job, error) {
	job, err := s.repo.GetByID(ctx, jobID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, job.WorkspaceID, actor); err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	switch job.Status {
	case JobStatusCreated, JobStatusQueued:
		if err := s.repo.MarkCanceled(ctx, job.ID, now); err != nil {
			return nil, err
		}
		job.Status = JobStatusCanceled
		job.FinishedAt = &now
		s.emit(ctx, *job, runevent.EventCanceled, nil)
	case JobStatusRunning:
		if err := s.repo.RequestCancel(ctx, job.ID, now); err != nil {
			return nil, err
		}
		job.Status = JobStatusCancelRequested
		job.CancelRequestedAt = &now
		s.emit(ctx, *job, runevent.EventCancelRequested, nil)
	case JobStatusCancelRequested:
	default:
		return nil, httpx.NewAppError(httpx.CodeConflict, "job cannot be canceled from current status", http.StatusConflict, map[string]string{"status": job.Status}, nil)
	}
	s.record(ctx, audit.AuditLog{WorkspaceID: &job.WorkspaceID, UserID: &actor.UserID, Action: "job.cancel_requested", ResourceType: "job", ResourceID: job.ID.String(), Payload: map[string]any{"status": job.Status}})
	return s.repo.GetByID(ctx, job.ID)
}

func (s *Service) MarkRunning(ctx context.Context, job Job) error {
	now := time.Now().UTC()
	if err := s.repo.IncrementAttempt(ctx, job.ID); err != nil {
		return err
	}
	if err := s.repo.MarkRunning(ctx, job.ID, now); err != nil {
		return err
	}
	job.Status = JobStatusRunning
	job.StartedAt = &now
	s.emit(ctx, job, runevent.EventRunning, nil)
	return nil
}

func (s *Service) MarkHeartbeat(ctx context.Context, job Job) error {
	now := time.Now().UTC()
	if err := s.repo.MarkHeartbeat(ctx, job.ID, now); err != nil {
		return err
	}
	s.emit(ctx, job, runevent.EventHeartbeat, map[string]any{"heartbeat_at": now})
	return nil
}

func (s *Service) MarkSucceeded(ctx context.Context, job Job) error {
	now := time.Now().UTC()
	if err := s.repo.MarkSucceeded(ctx, job.ID, now); err != nil {
		return err
	}
	job.Status = JobStatusSucceeded
	job.FinishedAt = &now
	s.emit(ctx, job, runevent.EventSucceeded, nil)
	return nil
}

func (s *Service) MarkCompletedWithFailures(ctx context.Context, job Job, errMsg string) error {
	now := time.Now().UTC()
	if err := s.repo.MarkCompletedWithFailures(ctx, job.ID, now, errMsg); err != nil {
		return err
	}
	job.Status = JobStatusCompletedWithFailures
	job.FinishedAt = &now
	s.emit(ctx, job, runevent.EventCompletedWithFailures, map[string]any{"error_message": errMsg})
	return nil
}

func (s *Service) MarkFailed(ctx context.Context, job Job, errMsg string) error {
	now := time.Now().UTC()
	if err := s.repo.MarkFailed(ctx, job.ID, now, errMsg); err != nil {
		return err
	}
	job.Status = JobStatusFailed
	job.FinishedAt = &now
	s.emit(ctx, job, runevent.EventFailed, map[string]any{"error_message": errMsg})
	return nil
}

func (s *Service) MarkCanceled(ctx context.Context, job Job) error {
	now := time.Now().UTC()
	if err := s.repo.MarkCanceled(ctx, job.ID, now); err != nil {
		return err
	}
	job.Status = JobStatusCanceled
	job.FinishedAt = &now
	s.emit(ctx, job, runevent.EventCanceled, nil)
	return nil
}

func (s *Service) emit(ctx context.Context, job Job, eventType string, payload map[string]any) {
	if s.events == nil {
		return
	}
	jobID := job.ID
	if payload == nil {
		payload = map[string]any{}
	}
	payload["job_id"] = job.ID.String()
	payload["job_type"] = job.JobType
	if _, err := s.events.Create(ctx, runevent.RunEvent{WorkspaceID: job.WorkspaceID, RunID: job.ResourceID, JobID: &jobID, EventType: eventType, Payload: payload}); err != nil {
		s.logger.Error("write run event failed", zap.String("event_type", eventType), zap.String("job_id", job.ID.String()), zap.Error(err))
	}
}

func (s *Service) record(ctx context.Context, log audit.AuditLog) {
	if s.audit != nil {
		s.audit.Record(ctx, log)
	}
}
