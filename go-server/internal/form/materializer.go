package form

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"

	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type FileGetter interface {
	GetByID(ctx context.Context, id uuid.UUID) (*filepkg.File, error)
}

type TemplateMaterializer struct {
	FormFileRepo FormFileRepo
	FileRepo     FileGetter
	Storage      storage.ObjectStorage
	Logger       *zap.Logger
}

func NewTemplateMaterializer(formRepo FormFileRepo, fileRepo FileGetter, objectStorage storage.ObjectStorage, logger *zap.Logger) *TemplateMaterializer {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &TemplateMaterializer{FormFileRepo: formRepo, FileRepo: fileRepo, Storage: objectStorage, Logger: logger}
}

func (m *TemplateMaterializer) MaterializeTemplate(ctx context.Context, workspaceID uuid.UUID, formFileID uuid.UUID, outDir string) (string, func(), error) {
	if m == nil || m.FormFileRepo == nil || m.FileRepo == nil || m.Storage == nil {
		return "", func() {}, fmt.Errorf("template materializer is not configured")
	}
	formFile, err := m.FormFileRepo.GetByID(ctx, formFileID)
	if err != nil {
		return "", func() {}, err
	}
	if formFile.WorkspaceID != workspaceID {
		return "", func() {}, httpx.NewAppError(httpx.CodeForbidden, "form file workspace mismatch", http.StatusForbidden, nil, nil)
	}
	record, err := m.FileRepo.GetByID(ctx, formFile.FileID)
	if err != nil {
		return "", func() {}, err
	}
	if record.WorkspaceID != workspaceID || record.Status != filepkg.FileStatusActive || record.FileCategory != filepkg.FileCategoryFormTemplate {
		return "", func() {}, httpx.NewAppError(httpx.CodeForbidden, "form template is not available in workspace", http.StatusForbidden, nil, nil)
	}
	reader, _, err := m.Storage.Get(ctx, record.ObjectKey)
	if err != nil {
		return "", func() {}, httpx.NewAppError(httpx.CodeInternal, "read form template object failed", http.StatusInternalServerError, nil, err)
	}
	defer reader.Close()
	inputDir := filepath.Join(outDir, "input")
	if err := os.MkdirAll(inputDir, 0o755); err != nil {
		return "", func() {}, httpx.NewAppError(httpx.CodeInternal, "create fill run input dir failed", http.StatusInternalServerError, nil, err)
	}
	localPath := filepath.Join(inputDir, filepkg.SanitizeFilename(record.Filename))
	file, err := os.Create(localPath)
	if err != nil {
		return "", func() {}, httpx.NewAppError(httpx.CodeInternal, "create local form template failed", http.StatusInternalServerError, nil, err)
	}
	if _, err := io.Copy(file, reader); err != nil {
		_ = file.Close()
		return "", func() {}, httpx.NewAppError(httpx.CodeInternal, "write local form template failed", http.StatusInternalServerError, nil, err)
	}
	if err := file.Close(); err != nil {
		return "", func() {}, httpx.NewAppError(httpx.CodeInternal, "close local form template failed", http.StatusInternalServerError, nil, err)
	}
	return localPath, func() {}, nil
}
