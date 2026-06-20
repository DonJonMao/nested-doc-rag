package knowledge

import (
	"context"
	"net/http"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
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

type KnowledgeBaseService struct {
	repo       KnowledgeBaseRepo
	versions   KnowledgeIndexVersionRepo
	authorizer WorkspaceAuthorizer
	audit      *audit.Service
	logger     *zap.Logger
}

func NewKnowledgeBaseService(repo KnowledgeBaseRepo, versions KnowledgeIndexVersionRepo, authorizer WorkspaceAuthorizer, auditSvc *audit.Service, logger *zap.Logger) *KnowledgeBaseService {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &KnowledgeBaseService{repo: repo, versions: versions, authorizer: authorizer, audit: auditSvc, logger: logger}
}

func (s *KnowledgeBaseService) CreateKnowledgeBase(ctx context.Context, req CreateKnowledgeBaseRequest, actor auth.Principal) (*KnowledgeBase, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, req.WorkspaceID, actor); err != nil {
		return nil, err
	}
	name := strings.TrimSpace(req.Name)
	if name == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "name is required", http.StatusBadRequest, nil, nil)
	}
	kbID := uuid.New()
	collection := strings.TrimSpace(req.QdrantCollection)
	if collection == "" {
		collection = "kb_" + strings.ReplaceAll(kbID.String()[:8], "-", "")
	}
	namespace := strings.TrimSpace(req.Namespace)
	if namespace == "" {
		namespace = fallbackNamespace(name, kbID)
	}
	now := time.Now().UTC()
	kb := KnowledgeBase{
		ID:               kbID,
		WorkspaceID:      req.WorkspaceID,
		Name:             name,
		Namespace:        namespace,
		Description:      strings.TrimSpace(req.Description),
		QdrantCollection: collection,
		Status:           KnowledgeBaseStatusEmpty,
		CreatedBy:        actor.UserID,
		CreatedAt:        now,
		UpdatedAt:        now,
	}
	if err := s.repo.Create(ctx, kb); err != nil {
		return nil, err
	}
	s.record(ctx, actor, kb.WorkspaceID, "knowledge_base.created", "knowledge_base", kb.ID.String(), map[string]any{"name": kb.Name, "qdrant_collection": kb.QdrantCollection})
	return s.repo.GetByID(ctx, kb.ID)
}

func (s *KnowledgeBaseService) GetKnowledgeBase(ctx context.Context, id uuid.UUID, actor auth.Principal) (*KnowledgeBase, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	kb, err := s.repo.GetByID(ctx, id)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, kb.WorkspaceID, actor); err != nil {
		return nil, err
	}
	return kb, nil
}

func (s *KnowledgeBaseService) ListKnowledgeBases(ctx context.Context, workspaceID uuid.UUID, limit int, offset int, actor auth.Principal) ([]KnowledgeBase, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, workspaceID, actor); err != nil {
		return nil, err
	}
	return s.repo.ListByWorkspace(ctx, workspaceID, limit, offset)
}

func (s *KnowledgeBaseService) ListKnowledgeBaseOptions(ctx context.Context, workspaceID uuid.UUID, limit int, offset int, actor auth.Principal) ([]KnowledgeBase, error) {
	if err := s.authorizer.CanReadWorkspace(ctx, workspaceID, actor); err != nil {
		return nil, err
	}
	return s.repo.ListOptionsByWorkspace(ctx, workspaceID, limit, offset)
}

func (s *KnowledgeBaseService) ListIndexVersions(ctx context.Context, kbID uuid.UUID, limit int, offset int, actor auth.Principal) ([]KnowledgeIndexVersion, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	kb, err := s.GetKnowledgeBase(ctx, kbID, actor)
	if err != nil {
		return nil, err
	}
	return s.versions.ListByKnowledgeBase(ctx, kb.ID, limit, offset)
}

func (s *KnowledgeBaseService) SetCurrentIndexVersion(ctx context.Context, kbID uuid.UUID, versionID uuid.UUID, actor auth.Principal) (*KnowledgeBase, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	kb, err := s.repo.GetByID(ctx, kbID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, kb.WorkspaceID, actor); err != nil {
		return nil, err
	}
	version, err := s.versions.GetByID(ctx, versionID)
	if err != nil {
		return nil, err
	}
	if version.KnowledgeBaseID != kb.ID || version.WorkspaceID != kb.WorkspaceID {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "index version workspace mismatch", http.StatusForbidden, nil, nil)
	}
	if version.Status != IndexVersionStatusReady {
		return nil, httpx.NewAppError(httpx.CodeConflict, "index version is not ready", http.StatusConflict, map[string]string{"status": version.Status}, nil)
	}
	if err := s.repo.UpdateCurrentIndexVersion(ctx, kb.ID, version.ID); err != nil {
		return nil, err
	}
	s.record(ctx, actor, kb.WorkspaceID, "knowledge_base.current_version_updated", "knowledge_base", kb.ID.String(), map[string]any{"index_version_id": version.ID.String()})
	return s.repo.GetByID(ctx, kb.ID)
}

func (s *KnowledgeBaseService) record(ctx context.Context, actor auth.Principal, workspaceID uuid.UUID, action string, resourceType string, resourceID string, payload map[string]any) {
	if s.audit != nil {
		s.audit.Record(ctx, audit.AuditLog{WorkspaceID: &workspaceID, UserID: &actor.UserID, Action: action, ResourceType: resourceType, ResourceID: resourceID, Payload: payload})
	}
}

type KnowledgeDocumentService struct {
	bases      KnowledgeBaseRepo
	docs       KnowledgeDocumentRepo
	files      FileUploader
	authorizer WorkspaceAuthorizer
	audit      *audit.Service
	logger     *zap.Logger
}

func NewKnowledgeDocumentService(bases KnowledgeBaseRepo, docs KnowledgeDocumentRepo, files FileUploader, authorizer WorkspaceAuthorizer, auditSvc *audit.Service, logger *zap.Logger) *KnowledgeDocumentService {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &KnowledgeDocumentService{bases: bases, docs: docs, files: files, authorizer: authorizer, audit: auditSvc, logger: logger}
}

func (s *KnowledgeDocumentService) UploadDocument(ctx context.Context, req UploadDocumentRequest, actor auth.Principal) (*KnowledgeDocument, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	kb, err := s.bases.GetByID(ctx, req.KnowledgeBaseID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, kb.WorkspaceID, actor); err != nil {
		return nil, err
	}
	role := strings.TrimSpace(req.DocumentRole)
	if !ValidDocumentRole(role) {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid document_role", http.StatusBadRequest, map[string]string{"document_role": role}, nil)
	}
	namespace := strings.TrimSpace(req.Namespace)
	if namespace == "" {
		namespace = strings.TrimSpace(kb.Namespace)
	}
	if namespace == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "knowledge base namespace is required", http.StatusBadRequest, nil, nil)
	}
	file, err := s.files.Upload(ctx, filepkg.UploadFileRequest{
		WorkspaceID:      kb.WorkspaceID,
		OriginalFilename: req.OriginalFilename,
		Size:             req.Size,
		MIMEType:         req.MIMEType,
		Category:         filepkg.FileCategoryKnowledgeDocument,
		Reader:           req.Reader,
	}, actor)
	if err != nil {
		return nil, err
	}
	doc := KnowledgeDocument{
		ID:              uuid.New(),
		KnowledgeBaseID: kb.ID,
		WorkspaceID:     kb.WorkspaceID,
		FileID:          file.ID,
		Filename:        file.Filename,
		DocumentRole:    role,
		Namespace:       namespace,
		Status:          KnowledgeDocumentStatusUploaded,
		CreatedBy:       actor.UserID,
		CreatedAt:       time.Now().UTC(),
		UpdatedAt:       time.Now().UTC(),
	}
	if err := s.docs.Create(ctx, doc); err != nil {
		return nil, err
	}
	_ = s.bases.UpdateStatus(ctx, kb.ID, KnowledgeBaseStatusStale)
	s.record(ctx, actor, doc.WorkspaceID, "knowledge_document.uploaded", "knowledge_document", doc.ID.String(), map[string]any{"knowledge_base_id": kb.ID.String(), "file_id": doc.FileID.String(), "namespace": doc.Namespace, "document_role": doc.DocumentRole})
	return &doc, nil
}

func (s *KnowledgeDocumentService) RegisterExistingFileAsDocument(ctx context.Context, kbID uuid.UUID, fileID uuid.UUID, documentRole string, namespace string, actor auth.Principal) (*KnowledgeDocument, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	kb, err := s.bases.GetByID(ctx, kbID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, kb.WorkspaceID, actor); err != nil {
		return nil, err
	}
	role := strings.TrimSpace(documentRole)
	if !ValidDocumentRole(role) {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid document_role", http.StatusBadRequest, map[string]string{"document_role": role}, nil)
	}
	namespace = strings.TrimSpace(namespace)
	if namespace == "" {
		namespace = strings.TrimSpace(kb.Namespace)
	}
	if namespace == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "knowledge base namespace is required", http.StatusBadRequest, nil, nil)
	}
	file, err := s.files.Get(ctx, fileID, actor)
	if err != nil {
		return nil, err
	}
	if file.WorkspaceID != kb.WorkspaceID || file.FileCategory != filepkg.FileCategoryKnowledgeDocument || file.Status != filepkg.FileStatusActive {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "file is not an active knowledge document in workspace", http.StatusBadRequest, nil, nil)
	}
	doc := KnowledgeDocument{
		ID:              uuid.New(),
		KnowledgeBaseID: kb.ID,
		WorkspaceID:     kb.WorkspaceID,
		FileID:          file.ID,
		Filename:        file.Filename,
		DocumentRole:    role,
		Namespace:       namespace,
		Status:          KnowledgeDocumentStatusUploaded,
		CreatedBy:       actor.UserID,
		CreatedAt:       time.Now().UTC(),
		UpdatedAt:       time.Now().UTC(),
	}
	if err := s.docs.Create(ctx, doc); err != nil {
		return nil, err
	}
	_ = s.bases.UpdateStatus(ctx, kb.ID, KnowledgeBaseStatusStale)
	s.record(ctx, actor, doc.WorkspaceID, "knowledge_document.registered", "knowledge_document", doc.ID.String(), map[string]any{"knowledge_base_id": kb.ID.String(), "file_id": doc.FileID.String()})
	return &doc, nil
}

func (s *KnowledgeDocumentService) ListDocuments(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int, actor auth.Principal) ([]KnowledgeDocument, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	kb, err := s.bases.GetByID(ctx, kbID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, kb.WorkspaceID, actor); err != nil {
		return nil, err
	}
	status = strings.TrimSpace(status)
	if status != "" && !ValidKnowledgeDocumentStatus(status) {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid knowledge document status", http.StatusBadRequest, map[string]string{"status": status}, nil)
	}
	return s.docs.ListByKnowledgeBase(ctx, kb.ID, status, limit, offset)
}

func (s *KnowledgeDocumentService) DeleteDocument(ctx context.Context, docID uuid.UUID, actor auth.Principal) (*KnowledgeDocument, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	doc, err := s.docs.GetByID(ctx, docID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, doc.WorkspaceID, actor); err != nil {
		return nil, err
	}
	if err := s.docs.SoftDelete(ctx, doc.ID); err != nil {
		return nil, err
	}
	nextStatus := KnowledgeBaseStatusStale
	if active, err := s.docs.ListActiveByKnowledgeBase(ctx, doc.KnowledgeBaseID); err == nil && len(active) == 0 {
		nextStatus = KnowledgeBaseStatusEmpty
	}
	_ = s.bases.UpdateStatus(ctx, doc.KnowledgeBaseID, nextStatus)
	if deleted, err := s.docs.GetByID(ctx, doc.ID); err == nil {
		doc = deleted
	}
	s.record(ctx, actor, doc.WorkspaceID, "knowledge_document.deleted", "knowledge_document", doc.ID.String(), map[string]any{"knowledge_base_id": doc.KnowledgeBaseID.String(), "file_id": doc.FileID.String()})
	return doc, nil
}

func (s *KnowledgeDocumentService) record(ctx context.Context, actor auth.Principal, workspaceID uuid.UUID, action string, resourceType string, resourceID string, payload map[string]any) {
	if s.audit != nil {
		s.audit.Record(ctx, audit.AuditLog{WorkspaceID: &workspaceID, UserID: &actor.UserID, Action: action, ResourceType: resourceType, ResourceID: resourceID, Payload: payload})
	}
}

type IngestionService struct {
	bases      KnowledgeBaseRepo
	docs       KnowledgeDocumentRepo
	versions   KnowledgeIndexVersionRepo
	ingestions IngestionJobRepo
	jobs       JobService
	authorizer WorkspaceAuthorizer
	audit      *audit.Service
	logger     *zap.Logger
	cfg        config.Config
}

func NewIngestionService(bases KnowledgeBaseRepo, docs KnowledgeDocumentRepo, versions KnowledgeIndexVersionRepo, ingestions IngestionJobRepo, jobs JobService, authorizer WorkspaceAuthorizer, auditSvc *audit.Service, logger *zap.Logger, cfg config.Config) *IngestionService {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &IngestionService{bases: bases, docs: docs, versions: versions, ingestions: ingestions, jobs: jobs, authorizer: authorizer, audit: auditSvc, logger: logger, cfg: cfg}
}

func (s *IngestionService) CreateIngestionRun(ctx context.Context, req CreateIngestionRunRequest, actor auth.Principal) (*IngestionJob, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	kb, err := s.bases.GetByID(ctx, req.KnowledgeBaseID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, kb.WorkspaceID, actor); err != nil {
		return nil, err
	}
	if !s.cfg.Python.IngestCommandEnabled {
		return nil, httpx.NewAppError(httpx.CodeFeatureDisabled, "ingest-knowledge command is disabled", http.StatusConflict, nil, nil)
	}
	documents, err := s.docs.ListActiveByKnowledgeBase(ctx, kb.ID)
	if err != nil {
		return nil, err
	}
	if len(documents) == 0 {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "knowledge base has no active documents", http.StatusBadRequest, nil, nil)
	}
	versionNumber, err := s.versions.NextVersion(ctx, kb.ID)
	if err != nil {
		return nil, err
	}
	namespace := defaultString(req.Namespace, kb.Namespace)
	if namespace == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "knowledge base namespace is required", http.StatusBadRequest, nil, nil)
	}
	qdrantCollection := defaultString(req.QdrantCollection, kb.QdrantCollection)
	if qdrantCollection == "" {
		qdrantCollection = "kb_" + strings.ReplaceAll(kb.ID.String()[:8], "-", "")
	}
	qdrantNamespace := defaultString(req.QdrantNamespace, namespace)
	versionID := uuid.New()
	version := KnowledgeIndexVersion{
		ID:               versionID,
		KnowledgeBaseID:  kb.ID,
		WorkspaceID:      kb.WorkspaceID,
		Version:          versionNumber,
		QdrantCollection: qdrantCollection,
		QdrantNamespace:  qdrantNamespace,
		Status:           IndexVersionStatusBuilding,
		DocumentCount:    len(documents),
		CreatedBy:        actor.UserID,
		CreatedAt:        time.Now().UTC(),
	}
	if err := s.versions.Create(ctx, version); err != nil {
		return nil, err
	}
	ingestionID := uuid.New()
	outDir := filepath.Join(s.cfg.Python.ProjectDir, "artifacts", "ingestion", ingestionID.String())
	ingestion := IngestionJob{
		ID:              ingestionID,
		WorkspaceID:     kb.WorkspaceID,
		KnowledgeBaseID: kb.ID,
		IndexVersionID:  &versionID,
		Status:          IngestionJobStatusCreated,
		DocumentCount:   len(documents),
		PythonCommand:   "python -m nested_doc_rag.cli ingest-knowledge",
		OutDir:          outDir,
		CreatedBy:       actor.UserID,
		CreatedAt:       time.Now().UTC(),
		UpdatedAt:       time.Now().UTC(),
	}
	if err := s.ingestions.Create(ctx, ingestion); err != nil {
		_ = s.versions.MarkFailed(context.Background(), version.ID, err.Error(), time.Now().UTC())
		return nil, err
	}
	job, err := s.jobs.CreateJob(ctx, jobs.CreateJobRequest{
		WorkspaceID:  ingestion.WorkspaceID,
		JobType:      jobs.JobTypeIngestKnowledge,
		ResourceType: jobs.ResourceTypeKnowledgeBase,
		ResourceID:   ingestion.ID,
		Payload:      BuildIngestKnowledgeJobPayload(ingestion, *kb, version, req, s.cfg),
		MaxAttempts:  s.cfg.Jobs.MaxAttempts,
	}, actor)
	if err != nil {
		_ = s.ingestions.MarkFailed(context.Background(), ingestion.ID, time.Now().UTC(), err.Error())
		_ = s.versions.MarkFailed(context.Background(), version.ID, err.Error(), time.Now().UTC())
		return nil, err
	}
	if err := s.ingestions.AttachJob(ctx, ingestion.ID, job.ID, time.Now().UTC()); err != nil {
		return nil, err
	}
	_ = s.bases.UpdateStatus(ctx, kb.ID, KnowledgeBaseStatusBuilding)
	s.record(ctx, actor, ingestion.WorkspaceID, "ingestion.created", "ingestion_job", ingestion.ID.String(), map[string]any{"knowledge_base_id": kb.ID.String(), "index_version_id": version.ID.String(), "job_id": job.ID.String()})
	return s.ingestions.GetByID(ctx, ingestion.ID)
}

func (s *IngestionService) GetIngestionJob(ctx context.Context, id uuid.UUID, actor auth.Principal) (*IngestionJob, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	ingestion, err := s.ingestions.GetByID(ctx, id)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, ingestion.WorkspaceID, actor); err != nil {
		return nil, err
	}
	return ingestion, nil
}

func (s *IngestionService) ListIngestionJobs(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int, actor auth.Principal) ([]IngestionJob, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	kb, err := s.bases.GetByID(ctx, kbID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, kb.WorkspaceID, actor); err != nil {
		return nil, err
	}
	status = strings.TrimSpace(status)
	if status != "" && !ValidIngestionJobStatus(status) {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid ingestion job status", http.StatusBadRequest, map[string]string{"status": status}, nil)
	}
	return s.ingestions.ListByKnowledgeBase(ctx, kb.ID, status, limit, offset)
}

func (s *IngestionService) CancelIngestionJob(ctx context.Context, id uuid.UUID, actor auth.Principal) (*IngestionJob, error) {
	if err := requireAdmin(actor); err != nil {
		return nil, err
	}
	ingestion, err := s.ingestions.GetByID(ctx, id)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, ingestion.WorkspaceID, actor); err != nil {
		return nil, err
	}
	if ingestion.JobID == nil {
		return nil, httpx.NewAppError(httpx.CodeConflict, "ingestion job has no worker job", http.StatusConflict, nil, nil)
	}
	job, err := s.jobs.CancelJob(ctx, *ingestion.JobID, actor)
	if err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	if job.Status == jobs.JobStatusCanceled {
		_ = s.ingestions.MarkCanceled(ctx, ingestion.ID, now)
	} else {
		_ = s.ingestions.RequestCancel(ctx, ingestion.ID, now)
	}
	s.record(ctx, actor, ingestion.WorkspaceID, "ingestion.cancel_requested", "ingestion_job", ingestion.ID.String(), map[string]any{"job_id": ingestion.JobID.String()})
	return s.ingestions.GetByID(ctx, ingestion.ID)
}

func (s *IngestionService) GetIndexVersion(ctx context.Context, id uuid.UUID, actor auth.Principal) (*KnowledgeIndexVersion, error) {
	version, err := s.versions.GetByID(ctx, id)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, version.WorkspaceID, actor); err != nil {
		return nil, err
	}
	return version, nil
}

func (s *IngestionService) record(ctx context.Context, actor auth.Principal, workspaceID uuid.UUID, action string, resourceType string, resourceID string, payload map[string]any) {
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

func requireAdmin(actor auth.Principal) error {
	if auth.IsAdminRoles(actor.Roles) {
		return nil
	}
	return httpx.NewAppError(httpx.CodeForbidden, "admin role required", http.StatusForbidden, nil, nil)
}

var namespaceUnsafe = regexp.MustCompile(`[^a-zA-Z0-9_]+`)

func fallbackNamespace(name string, id uuid.UUID) string {
	candidate := strings.ToLower(namespaceUnsafe.ReplaceAllString(strings.TrimSpace(name), "_"))
	candidate = strings.Trim(candidate, "_")
	if candidate != "" {
		return candidate
	}
	return "kb_" + strings.ReplaceAll(id.String()[:8], "-", "")
}
