package knowledge

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type FileGetter interface {
	GetByID(ctx context.Context, id uuid.UUID) (*filepkg.File, error)
}

type IngestionMaterializer struct {
	KnowledgeBaseRepo     KnowledgeBaseRepo
	KnowledgeDocumentRepo KnowledgeDocumentRepo
	FileRepo              FileGetter
	Storage               storage.ObjectStorage
	Logger                *zap.Logger
}

func NewIngestionMaterializer(kbRepo KnowledgeBaseRepo, docRepo KnowledgeDocumentRepo, fileRepo FileGetter, objectStorage storage.ObjectStorage, logger *zap.Logger) *IngestionMaterializer {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &IngestionMaterializer{KnowledgeBaseRepo: kbRepo, KnowledgeDocumentRepo: docRepo, FileRepo: fileRepo, Storage: objectStorage, Logger: logger}
}

func (m *IngestionMaterializer) MaterializeDocuments(ctx context.Context, workspaceID uuid.UUID, knowledgeBaseID uuid.UUID, outDir string) (string, int, func(), error) {
	if m == nil || m.KnowledgeBaseRepo == nil || m.KnowledgeDocumentRepo == nil || m.FileRepo == nil || m.Storage == nil {
		return "", 0, func() {}, fmt.Errorf("ingestion materializer is not configured")
	}
	kb, err := m.KnowledgeBaseRepo.GetByID(ctx, knowledgeBaseID)
	if err != nil {
		return "", 0, func() {}, err
	}
	if kb.WorkspaceID != workspaceID {
		return "", 0, func() {}, httpx.NewAppError(httpx.CodeForbidden, "knowledge base workspace mismatch", http.StatusForbidden, nil, nil)
	}
	documents, err := m.KnowledgeDocumentRepo.ListActiveByKnowledgeBase(ctx, kb.ID)
	if err != nil {
		return "", 0, func() {}, err
	}
	if len(documents) == 0 {
		return "", 0, func() {}, httpx.NewAppError(httpx.CodeInvalidArgument, "knowledge base has no active documents", http.StatusBadRequest, nil, nil)
	}
	inputDir := filepath.Join(outDir, "input")
	if err := os.MkdirAll(inputDir, 0o755); err != nil {
		return "", 0, func() {}, httpx.NewAppError(httpx.CodeInternal, "create ingestion input dir failed", http.StatusInternalServerError, nil, err)
	}
	for _, doc := range documents {
		if doc.WorkspaceID != workspaceID || doc.KnowledgeBaseID != kb.ID || doc.Status == KnowledgeDocumentStatusDeleted {
			continue
		}
		record, err := m.FileRepo.GetByID(ctx, doc.FileID)
		if err != nil {
			return "", 0, func() {}, err
		}
		if record.WorkspaceID != workspaceID || record.Status != filepkg.FileStatusActive || record.FileCategory != filepkg.FileCategoryKnowledgeDocument {
			return "", 0, func() {}, httpx.NewAppError(httpx.CodeForbidden, "knowledge document file is not available in workspace", http.StatusForbidden, nil, nil)
		}
		reader, _, err := m.Storage.Get(ctx, record.ObjectKey)
		if err != nil {
			return "", 0, func() {}, httpx.NewAppError(httpx.CodeInternal, "read knowledge document object failed", http.StatusInternalServerError, nil, err)
		}
		namespaceDir := filepath.Join(inputDir, safePathSegment(doc.Namespace))
		if err := os.MkdirAll(namespaceDir, 0o755); err != nil {
			_ = reader.Close()
			return "", 0, func() {}, httpx.NewAppError(httpx.CodeInternal, "create namespace input dir failed", http.StatusInternalServerError, nil, err)
		}
		localPath := filepath.Join(namespaceDir, filepkg.SanitizeFilename(record.Filename))
		if err := writeObjectToFile(localPath, reader); err != nil {
			return "", 0, func() {}, err
		}
	}
	return inputDir, len(documents), func() {}, nil
}

func writeObjectToFile(path string, reader io.ReadCloser) error {
	defer reader.Close()
	file, err := os.Create(path)
	if err != nil {
		return httpx.NewAppError(httpx.CodeInternal, "create local knowledge document failed", http.StatusInternalServerError, nil, err)
	}
	if _, err := io.Copy(file, reader); err != nil {
		_ = file.Close()
		return httpx.NewAppError(httpx.CodeInternal, "write local knowledge document failed", http.StatusInternalServerError, nil, err)
	}
	if err := file.Close(); err != nil {
		return httpx.NewAppError(httpx.CodeInternal, "close local knowledge document failed", http.StatusInternalServerError, nil, err)
	}
	return nil
}

func safePathSegment(value string) string {
	value = filepkg.SanitizeFilename(strings.TrimSpace(value))
	if value == "" {
		return "default"
	}
	return value
}
