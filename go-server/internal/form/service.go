package form

import (
	"context"
	"net/http"
	"path/filepath"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	knowledgepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type WorkspaceAuthorizer interface {
	CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
	CanWriteWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
}

type FileUploader interface {
	Upload(ctx context.Context, req filepkg.UploadFileRequest, actor auth.Principal) (*filepkg.File, error)
	Get(ctx context.Context, fileID uuid.UUID, actor auth.Principal) (*filepkg.File, error)
}

type JobService interface {
	CreateJob(ctx context.Context, req jobs.CreateJobRequest, actor auth.Principal) (*jobs.Job, error)
	CancelJob(ctx context.Context, jobID uuid.UUID, actor auth.Principal) (*jobs.Job, error)
}

type ArtifactService interface {
	ListRunArtifacts(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, actor auth.Principal) ([]artifact.RunArtifact, error)
	DownloadArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error)
	DownloadArtifactProxy(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error)
	OpenArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*artifact.DownloadResult, error)
}

type KnowledgeBaseReader interface {
	GetByID(ctx context.Context, id uuid.UUID) (*knowledgepkg.KnowledgeBase, error)
}

type FormFileService struct {
	repo       FormFileRepo
	files      FileUploader
	authorizer WorkspaceAuthorizer
	audit      *audit.Service
	logger     *zap.Logger
}

func NewFormFileService(repo FormFileRepo, files FileUploader, authorizer WorkspaceAuthorizer, auditSvc *audit.Service, logger *zap.Logger) *FormFileService {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &FormFileService{repo: repo, files: files, authorizer: authorizer, audit: auditSvc, logger: logger}
}

func (s *FormFileService) UploadForm(ctx context.Context, req UploadFormRequest, actor auth.Principal) (*FormFile, error) {
	if err := s.authorizer.CanWriteWorkspace(ctx, req.WorkspaceID, actor); err != nil {
		return nil, err
	}
	file, err := s.files.Upload(ctx, filepkg.UploadFileRequest{
		WorkspaceID:      req.WorkspaceID,
		OriginalFilename: req.OriginalFilename,
		Size:             req.Size,
		MIMEType:         req.MIMEType,
		Category:         filepkg.FileCategoryFormTemplate,
		Reader:           req.Reader,
	}, actor)
	if err != nil {
		return nil, err
	}
	formFile := FormFile{
		ID:          uuid.New(),
		WorkspaceID: req.WorkspaceID,
		FileID:      file.ID,
		Filename:    file.Filename,
		CreatedBy:   actor.UserID,
		CreatedAt:   time.Now().UTC(),
	}
	if err := s.repo.Create(ctx, formFile); err != nil {
		return nil, err
	}
	s.record(ctx, actor, formFile.WorkspaceID, "form.uploaded", "form_file", formFile.ID.String(), map[string]any{"file_id": formFile.FileID.String(), "filename": formFile.Filename})
	return &formFile, nil
}

func (s *FormFileService) RegisterExistingFileAsForm(ctx context.Context, workspaceID uuid.UUID, fileID uuid.UUID, actor auth.Principal) (*FormFile, error) {
	if err := s.authorizer.CanWriteWorkspace(ctx, workspaceID, actor); err != nil {
		return nil, err
	}
	file, err := s.files.Get(ctx, fileID, actor)
	if err != nil {
		return nil, err
	}
	if file.WorkspaceID != workspaceID || file.FileCategory != filepkg.FileCategoryFormTemplate || file.Status != filepkg.FileStatusActive {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "file is not an active form template in workspace", http.StatusBadRequest, nil, nil)
	}
	formFile := FormFile{ID: uuid.New(), WorkspaceID: workspaceID, FileID: file.ID, Filename: file.Filename, CreatedBy: actor.UserID, CreatedAt: time.Now().UTC()}
	if err := s.repo.Create(ctx, formFile); err != nil {
		return nil, err
	}
	s.record(ctx, actor, workspaceID, "form.registered", "form_file", formFile.ID.String(), map[string]any{"file_id": file.ID.String(), "filename": formFile.Filename})
	return &formFile, nil
}

func (s *FormFileService) GetForm(ctx context.Context, formID uuid.UUID, actor auth.Principal) (*FormFile, error) {
	formFile, err := s.repo.GetByID(ctx, formID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, formFile.WorkspaceID, actor); err != nil {
		return nil, err
	}
	return formFile, nil
}

func (s *FormFileService) ListForms(ctx context.Context, workspaceID uuid.UUID, limit int, offset int, actor auth.Principal) ([]FormFile, error) {
	if err := s.authorizer.CanReadWorkspace(ctx, workspaceID, actor); err != nil {
		return nil, err
	}
	return s.repo.ListByWorkspace(ctx, workspaceID, limit, offset)
}

func (s *FormFileService) record(ctx context.Context, actor auth.Principal, workspaceID uuid.UUID, action string, resourceType string, resourceID string, payload map[string]any) {
	if s.audit != nil {
		s.audit.Record(ctx, audit.AuditLog{WorkspaceID: &workspaceID, UserID: &actor.UserID, Action: action, ResourceType: resourceType, ResourceID: resourceID, Payload: payload})
	}
}

type FillRunService struct {
	repo       FillRunRepo
	forms      FormFileRepo
	jobs       JobService
	artifacts  ArtifactService
	authorizer WorkspaceAuthorizer
	audit      *audit.Service
	logger     *zap.Logger
	cfg        config.Config
	kbs        KnowledgeBaseReader
}

func NewFillRunService(repo FillRunRepo, forms FormFileRepo, jobs JobService, artifacts ArtifactService, authorizer WorkspaceAuthorizer, auditSvc *audit.Service, logger *zap.Logger, cfg config.Config) *FillRunService {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &FillRunService{repo: repo, forms: forms, jobs: jobs, artifacts: artifacts, authorizer: authorizer, audit: auditSvc, logger: logger, cfg: cfg}
}

func (s *FillRunService) SetKnowledgeBaseReader(reader KnowledgeBaseReader) {
	s.kbs = reader
}

func (s *FillRunService) CreateFillRun(ctx context.Context, req CreateFillRunRequest, actor auth.Principal) (*FillRun, error) {
	if err := s.authorizer.CanWriteWorkspace(ctx, req.WorkspaceID, actor); err != nil {
		return nil, err
	}
	formFile, err := s.forms.GetByID(ctx, req.FormFileID)
	if err != nil {
		return nil, err
	}
	if formFile.WorkspaceID != req.WorkspaceID {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "form file workspace mismatch", http.StatusForbidden, nil, nil)
	}
	if err := s.productizeNonAdminFillRequest(ctx, &req, actor); err != nil {
		return nil, err
	}
	if strings.TrimSpace(req.TargetNamespace) == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "target_namespace is required", http.StatusBadRequest, nil, nil)
	}
	runName, err := normalizeFillRunName(req.Name, formFile.Filename)
	if err != nil {
		return nil, err
	}
	writeback := true
	if req.Writeback != nil {
		writeback = *req.Writeback
	}
	runID := uuid.New()
	run := FillRun{
		ID:               runID,
		WorkspaceID:      req.WorkspaceID,
		FormFileID:       req.FormFileID,
		Name:             runName,
		KnowledgeBaseID:  req.KnowledgeBaseID,
		IndexVersionID:   req.IndexVersionID,
		TargetNamespace:  strings.TrimSpace(req.TargetNamespace),
		GlobalNamespace:  defaultString(req.GlobalNamespace, "global"),
		RoomContext:      strings.TrimSpace(req.RoomContext),
		RowsSpec:         defaultString(req.Rows, s.cfg.Python.Step15DefaultRows),
		RetrievalMode:    defaultString(req.RetrievalMode, s.cfg.Python.Step15DefaultRetrievalMode),
		PromptVersion:    defaultString(req.PromptVersion, s.cfg.Python.Step15DefaultPromptVersion),
		JudgeEnabled:     req.Judge,
		UseJudgeCache:    req.UseJudgeCache,
		WritebackEnabled: writeback,
		Status:           FillRunStatusCreated,
		OutDir:           filepath.Join(s.cfg.Python.ProjectDir, "artifacts", "runs", runID.String()),
		CreatedBy:        actor.UserID,
		CreatedAt:        time.Now().UTC(),
		UpdatedAt:        time.Now().UTC(),
	}
	if err := s.repo.Create(ctx, run); err != nil {
		return nil, err
	}
	job, err := s.jobs.CreateJob(ctx, jobs.CreateJobRequest{
		WorkspaceID:  run.WorkspaceID,
		JobType:      jobs.JobTypeFillForm,
		ResourceType: jobs.ResourceTypeFillRun,
		ResourceID:   run.ID,
		Payload:      BuildFillFormJobPayload(run, *formFile, s.cfg),
		MaxAttempts:  s.cfg.Jobs.MaxAttempts,
	}, actor)
	if err != nil {
		_ = s.repo.MarkFailed(context.Background(), run.ID, time.Now().UTC(), err.Error())
		return nil, err
	}
	now := time.Now().UTC()
	if err := s.repo.AttachJob(ctx, run.ID, job.ID, now); err != nil {
		return nil, err
	}
	s.record(ctx, actor, run.WorkspaceID, "fill_run.created", "fill_run", run.ID.String(), map[string]any{"job_id": job.ID.String(), "form_file_id": run.FormFileID.String(), "name": run.Name})
	return s.repo.GetByID(ctx, run.ID)
}

func (s *FillRunService) productizeNonAdminFillRequest(ctx context.Context, req *CreateFillRunRequest, actor auth.Principal) error {
	if auth.IsAdminRoles(actor.Roles) {
		return nil
	}
	if req.KnowledgeBaseID == nil || *req.KnowledgeBaseID == uuid.Nil {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "knowledge_base_id is required for non-admin fill runs", http.StatusBadRequest, nil, nil)
	}
	if s.kbs == nil {
		return httpx.NewAppError(httpx.CodeInternal, "knowledge base reader is not configured", http.StatusInternalServerError, nil, nil)
	}
	kb, err := s.kbs.GetByID(ctx, *req.KnowledgeBaseID)
	if err != nil {
		return err
	}
	if kb.WorkspaceID != req.WorkspaceID {
		return httpx.NewAppError(httpx.CodeForbidden, "knowledge base workspace mismatch", http.StatusForbidden, nil, nil)
	}
	if kb.Status != knowledgepkg.KnowledgeBaseStatusReady {
		return httpx.NewAppError(httpx.CodeConflict, "knowledge base is not ready", http.StatusConflict, map[string]string{"status": kb.Status}, nil)
	}
	if kb.CurrentIndexVersionID == nil {
		return httpx.NewAppError(httpx.CodeConflict, "knowledge base has no current index version", http.StatusConflict, nil, nil)
	}
	if strings.TrimSpace(kb.Namespace) == "" {
		return httpx.NewAppError(httpx.CodeConflict, "knowledge base namespace is empty", http.StatusConflict, nil, nil)
	}
	if req.IndexVersionID != nil && *req.IndexVersionID != *kb.CurrentIndexVersionID {
		return httpx.NewAppError(httpx.CodeConflict, "index version is not current for knowledge base", http.StatusConflict, nil, nil)
	}
	if target := strings.TrimSpace(req.TargetNamespace); target != "" && target != kb.Namespace {
		return httpx.NewAppError(httpx.CodeConflict, "target namespace does not match knowledge base", http.StatusConflict, nil, nil)
	}
	writeback := true
	req.KnowledgeBaseID = &kb.ID
	req.IndexVersionID = kb.CurrentIndexVersionID
	req.TargetNamespace = kb.Namespace
	req.GlobalNamespace = "global"
	req.Rows = s.cfg.Python.Step15DefaultRows
	req.RetrievalMode = s.cfg.Python.Step15DefaultRetrievalMode
	req.PromptVersion = s.cfg.Python.Step15DefaultPromptVersion
	req.Judge = false
	req.UseJudgeCache = false
	req.Writeback = &writeback
	return nil
}

func (s *FillRunService) CreateSimpleFillRun(ctx context.Context, req CreateSimpleFillRunRequest, actor auth.Principal) (*FillRun, error) {
	if req.KnowledgeBaseID == uuid.Nil {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "knowledge_base_id is required", http.StatusBadRequest, nil, nil)
	}
	if s.kbs == nil {
		return nil, httpx.NewAppError(httpx.CodeInternal, "knowledge base reader is not configured", http.StatusInternalServerError, nil, nil)
	}
	kb, err := s.kbs.GetByID(ctx, req.KnowledgeBaseID)
	if err != nil {
		return nil, err
	}
	if kb.WorkspaceID != req.WorkspaceID {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "knowledge base workspace mismatch", http.StatusForbidden, nil, nil)
	}
	if err := s.authorizer.CanReadWorkspace(ctx, kb.WorkspaceID, actor); err != nil {
		return nil, err
	}
	if kb.Status != knowledgepkg.KnowledgeBaseStatusReady {
		return nil, httpx.NewAppError(httpx.CodeConflict, "knowledge base is not ready", http.StatusConflict, map[string]string{"status": kb.Status}, nil)
	}
	if kb.CurrentIndexVersionID == nil {
		return nil, httpx.NewAppError(httpx.CodeConflict, "knowledge base has no current index version", http.StatusConflict, nil, nil)
	}
	if strings.TrimSpace(kb.Namespace) == "" {
		return nil, httpx.NewAppError(httpx.CodeConflict, "knowledge base namespace is empty", http.StatusConflict, nil, nil)
	}
	writeback := true
	return s.CreateFillRun(ctx, CreateFillRunRequest{
		WorkspaceID:     req.WorkspaceID,
		FormFileID:      req.FormFileID,
		KnowledgeBaseID: &kb.ID,
		IndexVersionID:  kb.CurrentIndexVersionID,
		Name:            req.Name,
		TargetNamespace: kb.Namespace,
		GlobalNamespace: "global",
		RoomContext:     req.RoomContext,
		Rows:            s.cfg.Python.Step15DefaultRows,
		RetrievalMode:   s.cfg.Python.Step15DefaultRetrievalMode,
		PromptVersion:   s.cfg.Python.Step15DefaultPromptVersion,
		Judge:           false,
		UseJudgeCache:   false,
		Writeback:       &writeback,
	}, actor)
}

func (s *FillRunService) GetFillRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*FillRun, error) {
	run, err := s.repo.GetByID(ctx, runID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, run.WorkspaceID, actor); err != nil {
		return nil, err
	}
	if !auth.IsAdminRoles(actor.Roles) && run.CreatedBy != actor.UserID {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "fill run is not owned by current user", http.StatusForbidden, nil, nil)
	}
	return run, nil
}

func (s *FillRunService) ListFillRuns(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int, mine bool, actor auth.Principal) ([]FillRun, error) {
	if err := s.authorizer.CanReadWorkspace(ctx, workspaceID, actor); err != nil {
		return nil, err
	}
	if mine || !auth.IsAdminRoles(actor.Roles) {
		return s.repo.ListByWorkspaceAndCreator(ctx, workspaceID, actor.UserID, status, limit, offset)
	}
	return s.repo.ListByWorkspace(ctx, workspaceID, status, limit, offset)
}

func (s *FillRunService) CancelFillRun(ctx context.Context, runID uuid.UUID, actor auth.Principal) (*FillRun, error) {
	run, err := s.repo.GetByID(ctx, runID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, run.WorkspaceID, actor); err != nil {
		return nil, err
	}
	if !auth.IsAdminRoles(actor.Roles) && run.CreatedBy != actor.UserID {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "fill run is not owned by current user", http.StatusForbidden, nil, nil)
	}
	if run.JobID == nil {
		return nil, httpx.NewAppError(httpx.CodeConflict, "fill run has no job", http.StatusConflict, nil, nil)
	}
	job, err := s.jobs.CancelJob(ctx, *run.JobID, actor)
	if err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	if job.Status == jobs.JobStatusCanceled {
		_ = s.repo.MarkCanceled(ctx, run.ID, now)
	} else {
		_ = s.repo.RequestCancel(ctx, run.ID, now)
	}
	s.record(ctx, actor, run.WorkspaceID, "fill_run.cancel_requested", "fill_run", run.ID.String(), map[string]any{"job_id": run.JobID.String()})
	return s.repo.GetByID(ctx, run.ID)
}

func (s *FillRunService) GetFillRunArtifacts(ctx context.Context, runID uuid.UUID, actor auth.Principal) ([]artifact.RunArtifact, error) {
	run, err := s.GetFillRun(ctx, runID, actor)
	if err != nil {
		return nil, err
	}
	return s.artifacts.ListRunArtifacts(ctx, run.WorkspaceID, run.ID, actor)
}

func (s *FillRunService) GetDownloadArtifactByType(ctx context.Context, runID uuid.UUID, artifactType string, actor auth.Principal) (*artifact.DownloadResult, error) {
	run, err := s.GetFillRun(ctx, runID, actor)
	if err != nil {
		return nil, err
	}
	artifacts, err := s.artifacts.ListRunArtifacts(ctx, run.WorkspaceID, run.ID, actor)
	if err != nil {
		return nil, err
	}
	for _, item := range artifacts {
		if item.ArtifactType == artifactType {
			return s.artifacts.DownloadArtifact(ctx, item.ID, actor)
		}
	}
	return nil, httpx.NewAppError(httpx.CodeNotFound, "artifact not found", http.StatusNotFound, map[string]string{"artifact_type": artifactType}, nil)
}

func (s *FillRunService) record(ctx context.Context, actor auth.Principal, workspaceID uuid.UUID, action string, resourceType string, resourceID string, payload map[string]any) {
	if s.audit != nil {
		s.audit.Record(ctx, audit.AuditLog{WorkspaceID: &workspaceID, UserID: &actor.UserID, Action: action, ResourceType: resourceType, ResourceID: resourceID, Payload: payload})
	}
}

func defaultString(value string, fallback string) string {
	value = strings.TrimSpace(value)
	if value != "" {
		return value
	}
	return strings.TrimSpace(fallback)
}

func normalizeFillRunName(value string, fallbackFilename string) (string, error) {
	name := strings.TrimSpace(value)
	if name == "" {
		name = strings.TrimSuffix(filepath.Base(strings.TrimSpace(fallbackFilename)), filepath.Ext(fallbackFilename))
	}
	name = strings.TrimSpace(name)
	if name == "" {
		name = "未命名任务"
	}
	if len([]rune(name)) > 120 {
		return "", httpx.NewAppError(httpx.CodeInvalidArgument, "fill run name is too long", http.StatusBadRequest, map[string]int{"max_length": 120}, nil)
	}
	return name, nil
}
