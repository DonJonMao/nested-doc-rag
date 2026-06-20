package file

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/audit"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
	"github.com/google/uuid"
)

type WorkspaceAuthorizer interface {
	CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
	CanWriteWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
}

type Service struct {
	repo                     Repo
	storage                  storage.ObjectStorage
	authorizer               WorkspaceAuthorizer
	audit                    *audit.Service
	validator                *Validator
	tempDir                  string
	deleteObjectOnSoftDelete bool
}

func NewService(
	repo Repo,
	objectStorage storage.ObjectStorage,
	authorizer WorkspaceAuthorizer,
	auditSvc *audit.Service,
	validator *Validator,
	tempDir string,
	deleteObjectOnSoftDelete bool,
) *Service {
	return &Service{
		repo:                     repo,
		storage:                  objectStorage,
		authorizer:               authorizer,
		audit:                    auditSvc,
		validator:                validator,
		tempDir:                  tempDir,
		deleteObjectOnSoftDelete: deleteObjectOnSoftDelete,
	}
}

func (s *Service) Upload(ctx context.Context, req UploadFileRequest, actor auth.Principal) (*File, error) {
	if req.Reader == nil {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "file is required", http.StatusBadRequest, nil, nil)
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, req.WorkspaceID, actor); err != nil {
		return nil, err
	}
	if !ValidCategory(req.Category) {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid file category", http.StatusBadRequest, map[string]string{"file_category": req.Category}, nil)
	}
	if err := requireAdminForKnowledgeDocument(req.Category, actor); err != nil {
		return nil, err
	}
	if err := s.validator.ValidateUpload(req.OriginalFilename, req.Size, req.MIMEType); err != nil {
		return nil, err
	}
	if err := os.MkdirAll(s.tempDir, 0o755); err != nil {
		return nil, httpx.NewAppError(httpx.CodeInternal, "create upload temp dir failed", http.StatusInternalServerError, nil, err)
	}
	tmp, err := os.CreateTemp(s.tempDir, "upload-*")
	if err != nil {
		return nil, httpx.NewAppError(httpx.CodeInternal, "create upload temp file failed", http.StatusInternalServerError, nil, err)
	}
	tmpPath := tmp.Name()
	defer func() {
		_ = tmp.Close()
		_ = os.Remove(tmpPath)
	}()

	hasher := sha256.New()
	limited := io.LimitReader(req.Reader, s.validator.MaxUploadSize+1)
	written, err := io.Copy(io.MultiWriter(tmp, hasher), limited)
	if err != nil {
		return nil, httpx.NewAppError(httpx.CodeInternal, "read upload failed", http.StatusInternalServerError, nil, err)
	}
	if written > s.validator.MaxUploadSize {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "file is too large", http.StatusBadRequest, map[string]any{"max_upload_size": s.validator.MaxUploadSize}, nil)
	}
	if req.Size >= 0 && written != req.Size {
		req.Size = written
	}
	if _, err := tmp.Seek(0, io.SeekStart); err != nil {
		return nil, httpx.NewAppError(httpx.CodeInternal, "rewind upload temp file failed", http.StatusInternalServerError, nil, err)
	}
	detectedMIME := detectMIME(tmp)
	if err := s.validator.ValidateMIME(detectedMIME, req.OriginalFilename); err != nil && shouldRejectDetectedMIME(req.MIMEType, detectedMIME) {
		return nil, err
	}
	if _, err := tmp.Seek(0, io.SeekStart); err != nil {
		return nil, httpx.NewAppError(httpx.CodeInternal, "rewind upload temp file failed", http.StatusInternalServerError, nil, err)
	}

	fileID := uuid.New()
	filename := SanitizeFilename(req.OriginalFilename)
	mimeType := normalizeMIME(req.MIMEType)
	if mimeType == "" || mimeType == "application/octet-stream" {
		mimeType = detectedMIME
	}
	record := File{
		ID:               fileID,
		WorkspaceID:      req.WorkspaceID,
		Filename:         filename,
		OriginalFilename: req.OriginalFilename,
		ObjectKey:        BuildFileObjectKey(req.WorkspaceID, fileID, req.Category, filename),
		FileSize:         written,
		MIMEType:         mimeType,
		SHA256:           hex.EncodeToString(hasher.Sum(nil)),
		FileCategory:     req.Category,
		Status:           FileStatusActive,
		CreatedBy:        actor.UserID,
		CreatedAt:        time.Now().UTC(),
	}
	if record.ObjectKey == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid object key", http.StatusBadRequest, nil, nil)
	}
	if err := s.storage.Put(ctx, record.ObjectKey, tmp, record.FileSize, record.MIMEType); err != nil {
		return nil, httpx.NewAppError(httpx.CodeInternal, "store file failed", http.StatusInternalServerError, nil, err)
	}
	if err := s.repo.Create(ctx, record); err != nil {
		_ = s.storage.Delete(context.Background(), record.ObjectKey)
		return nil, err
	}
	s.record(ctx, audit.AuditLog{
		WorkspaceID:  &record.WorkspaceID,
		UserID:       &actor.UserID,
		Action:       "file.uploaded",
		ResourceType: "file",
		ResourceID:   record.ID.String(),
		Payload: map[string]any{
			"filename":      record.Filename,
			"file_size":     record.FileSize,
			"mime_type":     record.MIMEType,
			"sha256":        record.SHA256,
			"file_category": record.FileCategory,
		},
	})
	return &record, nil
}

func (s *Service) Get(ctx context.Context, fileID uuid.UUID, actor auth.Principal) (*File, error) {
	record, err := s.repo.GetByID(ctx, fileID)
	if err != nil {
		return nil, err
	}
	if err := requireAdminForKnowledgeDocument(record.FileCategory, actor); err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, record.WorkspaceID, actor); err != nil {
		return nil, err
	}
	return record, nil
}

func (s *Service) List(ctx context.Context, workspaceID uuid.UUID, category string, limit int, offset int, actor auth.Principal) ([]File, error) {
	if err := s.authorizer.CanReadWorkspace(ctx, workspaceID, actor); err != nil {
		return nil, err
	}
	if category != "" && !ValidCategory(category) {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid file category", http.StatusBadRequest, map[string]string{"file_category": category}, nil)
	}
	if !auth.IsAdminRoles(actor.Roles) && (category == "" || category == FileCategoryKnowledgeDocument) {
		return nil, httpx.NewAppError(httpx.CodeForbidden, "admin role required for knowledge documents", http.StatusForbidden, nil, nil)
	}
	return s.repo.ListByWorkspace(ctx, workspaceID, category, limit, offset)
}

func (s *Service) Download(ctx context.Context, fileID uuid.UUID, actor auth.Principal) (*DownloadResult, error) {
	record, err := s.Get(ctx, fileID, actor)
	if err != nil {
		return nil, err
	}
	if record.Status != FileStatusActive {
		return nil, httpx.NewAppError(httpx.CodeNotFound, "file not found", http.StatusNotFound, nil, nil)
	}
	reader, info, err := s.storage.Get(ctx, record.ObjectKey)
	if err != nil {
		return nil, httpx.NewAppError(httpx.CodeInternal, "read file failed", http.StatusInternalServerError, nil, err)
	}
	s.record(ctx, audit.AuditLog{
		WorkspaceID:  &record.WorkspaceID,
		UserID:       &actor.UserID,
		Action:       "file.downloaded",
		ResourceType: "file",
		ResourceID:   record.ID.String(),
		Payload:      map[string]any{"filename": record.Filename, "file_size": record.FileSize, "mime_type": record.MIMEType, "sha256": record.SHA256, "file_category": record.FileCategory},
	})
	contentType := record.MIMEType
	if contentType == "" {
		contentType = info.ContentType
	}
	return &DownloadResult{Filename: record.Filename, ContentType: contentType, ContentLength: record.FileSize, Reader: reader}, nil
}

func (s *Service) Delete(ctx context.Context, fileID uuid.UUID, actor auth.Principal) error {
	record, err := s.repo.GetByID(ctx, fileID)
	if err != nil {
		return err
	}
	if err := requireAdminForKnowledgeDocument(record.FileCategory, actor); err != nil {
		return err
	}
	if err := s.authorizer.CanWriteWorkspace(ctx, record.WorkspaceID, actor); err != nil {
		return err
	}
	deletedAt := time.Now().UTC()
	if err := s.repo.SoftDelete(ctx, fileID, deletedAt); err != nil {
		return err
	}
	if s.deleteObjectOnSoftDelete {
		_ = s.storage.Delete(context.Background(), record.ObjectKey)
	}
	s.record(ctx, audit.AuditLog{
		WorkspaceID:  &record.WorkspaceID,
		UserID:       &actor.UserID,
		Action:       "file.deleted",
		ResourceType: "file",
		ResourceID:   record.ID.String(),
		Payload:      map[string]any{"filename": record.Filename, "file_size": record.FileSize, "mime_type": record.MIMEType, "sha256": record.SHA256, "file_category": record.FileCategory},
	})
	return nil
}

func (s *Service) record(ctx context.Context, log audit.AuditLog) {
	if s.audit != nil {
		s.audit.Record(ctx, log)
	}
}

func requireAdminForKnowledgeDocument(category string, actor auth.Principal) error {
	if category == FileCategoryKnowledgeDocument && !auth.IsAdminRoles(actor.Roles) {
		return httpx.NewAppError(httpx.CodeForbidden, "admin role required for knowledge documents", http.StatusForbidden, nil, nil)
	}
	return nil
}

func detectMIME(reader io.ReadSeeker) string {
	buf := make([]byte, 512)
	n, err := reader.Read(buf)
	if err != nil && err != io.EOF {
		return "application/octet-stream"
	}
	return http.DetectContentType(buf[:n])
}

func normalizeMIME(mimeType string) string {
	return strings.ToLower(strings.TrimSpace(strings.Split(mimeType, ";")[0]))
}

func shouldRejectDetectedMIME(declared string, detected string) bool {
	declared = normalizeMIME(declared)
	detected = normalizeMIME(detected)
	if detected == "" || detected == "application/octet-stream" {
		return false
	}
	if declared == "" || declared == "application/octet-stream" {
		return true
	}
	return false
}
