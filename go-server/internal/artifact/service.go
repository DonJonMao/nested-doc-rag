package artifact

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
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
	"github.com/google/uuid"
)

type WorkspaceAuthorizer interface {
	CanReadWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
	CanWriteWorkspace(ctx context.Context, workspaceID uuid.UUID, actor auth.Principal) error
}

type Service struct {
	repo       Repo
	storage    storage.ObjectStorage
	authorizer WorkspaceAuthorizer
	audit      *audit.Service
}

func NewService(repo Repo, objectStorage storage.ObjectStorage, authorizer WorkspaceAuthorizer, auditSvc *audit.Service) *Service {
	return &Service{repo: repo, storage: objectStorage, authorizer: authorizer, audit: auditSvc}
}

func (s *Service) RegisterArtifact(ctx context.Context, req RegisterArtifactRequest, actor auth.Principal) (*RunArtifact, error) {
	if err := s.authorizer.CanWriteWorkspace(ctx, req.WorkspaceID, actor); err != nil {
		return nil, err
	}
	if strings.TrimSpace(req.ArtifactType) == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "artifact_type is required", http.StatusBadRequest, nil, nil)
	}
	filename := filepkg.SanitizeFilename(req.Filename)
	artifactID := uuid.New()
	record := RunArtifact{
		ID:           artifactID,
		WorkspaceID:  req.WorkspaceID,
		RunID:        req.RunID,
		ArtifactType: strings.TrimSpace(req.ArtifactType),
		Filename:     filename,
		ObjectKey:    req.ObjectKey,
		LocalPath:    req.LocalPath,
		ContentType:  strings.TrimSpace(req.ContentType),
		FileSize:     req.FileSize,
		SHA256:       req.SHA256,
		CreatedBy:    actor.UserID,
		CreatedAt:    time.Now().UTC(),
	}
	if record.ObjectKey == "" {
		record.ObjectKey = filepkg.BuildArtifactObjectKey(req.WorkspaceID, req.RunID, artifactID, filename)
	}
	if record.ObjectKey == "" {
		return nil, httpx.NewAppError(httpx.CodeInvalidArgument, "invalid artifact object key", http.StatusBadRequest, nil, nil)
	}
	if req.Reader != nil || req.LocalPath != "" {
		reader, closeFn, err := artifactReader(req)
		if err != nil {
			return nil, err
		}
		defer closeFn()
		data, err := readArtifactToTemp(reader)
		if err != nil {
			return nil, err
		}
		defer func() {
			_ = data.Reader.Close()
			_ = os.Remove(data.Path)
		}()
		record.FileSize = data.Size
		record.SHA256 = data.SHA256
		if record.ContentType == "" {
			record.ContentType = "application/octet-stream"
		}
		if err := s.storage.Put(ctx, record.ObjectKey, data.Reader, record.FileSize, record.ContentType); err != nil {
			return nil, httpx.NewAppError(httpx.CodeInternal, "store artifact failed", http.StatusInternalServerError, nil, err)
		}
	}
	if err := s.repo.Create(ctx, record); err != nil {
		if req.Reader != nil || req.LocalPath != "" {
			_ = s.storage.Delete(context.Background(), record.ObjectKey)
		}
		return nil, err
	}
	s.record(ctx, audit.AuditLog{
		WorkspaceID:  &record.WorkspaceID,
		UserID:       &actor.UserID,
		Action:       "artifact.registered",
		ResourceType: "run_artifact",
		ResourceID:   record.ID.String(),
		Payload:      map[string]any{"filename": record.Filename, "file_size": record.FileSize, "sha256": record.SHA256, "artifact_type": record.ArtifactType},
	})
	return &record, nil
}

func (s *Service) GetArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*RunArtifact, error) {
	record, err := s.repo.GetByID(ctx, artifactID)
	if err != nil {
		return nil, err
	}
	if err := s.authorizer.CanReadWorkspace(ctx, record.WorkspaceID, actor); err != nil {
		return nil, err
	}
	return record, nil
}

func (s *Service) ListRunArtifacts(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, actor auth.Principal) ([]RunArtifact, error) {
	if err := s.authorizer.CanReadWorkspace(ctx, workspaceID, actor); err != nil {
		return nil, err
	}
	return s.repo.ListByRun(ctx, workspaceID, runID)
}

func (s *Service) DownloadArtifact(ctx context.Context, artifactID uuid.UUID, actor auth.Principal) (*DownloadResult, error) {
	record, err := s.GetArtifact(ctx, artifactID, actor)
	if err != nil {
		return nil, err
	}
	reader, info, err := s.storage.Get(ctx, record.ObjectKey)
	if err != nil {
		return nil, httpx.NewAppError(httpx.CodeInternal, "read artifact failed", http.StatusInternalServerError, nil, err)
	}
	s.record(ctx, audit.AuditLog{
		WorkspaceID:  &record.WorkspaceID,
		UserID:       &actor.UserID,
		Action:       "artifact.downloaded",
		ResourceType: "run_artifact",
		ResourceID:   record.ID.String(),
		Payload:      map[string]any{"filename": record.Filename, "file_size": record.FileSize, "sha256": record.SHA256, "artifact_type": record.ArtifactType},
	})
	contentType := record.ContentType
	if contentType == "" {
		contentType = info.ContentType
	}
	return &DownloadResult{Filename: record.Filename, ContentType: contentType, ContentLength: record.FileSize, Reader: reader}, nil
}

func (s *Service) record(ctx context.Context, log audit.AuditLog) {
	if s.audit != nil {
		s.audit.Record(ctx, log)
	}
}

type tempArtifact struct {
	Path   string
	Reader *os.File
	Size   int64
	SHA256 string
}

func artifactReader(req RegisterArtifactRequest) (io.Reader, func(), error) {
	if req.Reader != nil {
		return req.Reader, func() {}, nil
	}
	file, err := os.Open(req.LocalPath)
	if err != nil {
		return nil, func() {}, httpx.NewAppError(httpx.CodeInvalidArgument, "artifact local path is not readable", http.StatusBadRequest, nil, err)
	}
	return file, func() { _ = file.Close() }, nil
}

func readArtifactToTemp(reader io.Reader) (*tempArtifact, error) {
	tmp, err := os.CreateTemp("", "artifact-*")
	if err != nil {
		return nil, httpx.NewAppError(httpx.CodeInternal, "create artifact temp file failed", http.StatusInternalServerError, nil, err)
	}
	hasher := sha256.New()
	size, err := io.Copy(io.MultiWriter(tmp, hasher), reader)
	if err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmp.Name())
		return nil, httpx.NewAppError(httpx.CodeInternal, "read artifact failed", http.StatusInternalServerError, nil, err)
	}
	if _, err := tmp.Seek(0, io.SeekStart); err != nil {
		_ = tmp.Close()
		_ = os.Remove(tmp.Name())
		return nil, httpx.NewAppError(httpx.CodeInternal, "rewind artifact temp file failed", http.StatusInternalServerError, nil, err)
	}
	return &tempArtifact{Path: tmp.Name(), Reader: tmp, Size: size, SHA256: hex.EncodeToString(hasher.Sum(nil))}, nil
}
