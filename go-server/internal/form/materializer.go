package form

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
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
	file, err := os.CreateTemp(inputDir, ".template-*")
	if err != nil {
		return "", func() {}, httpx.NewAppError(httpx.CodeInternal, "create local form template failed", http.StatusInternalServerError, nil, err)
	}
	defer os.Remove(file.Name())
	hasher := sha256.New()
	if _, err := io.Copy(io.MultiWriter(file, hasher), reader); err != nil {
		_ = file.Close()
		return "", func() {}, httpx.NewAppError(httpx.CodeInternal, "write local form template failed", http.StatusInternalServerError, nil, err)
	}
	if err := file.Close(); err != nil {
		return "", func() {}, httpx.NewAppError(httpx.CodeInternal, "close local form template failed", http.StatusInternalServerError, nil, err)
	}
	expectedHash := strings.TrimPrefix(strings.ToLower(strings.TrimSpace(record.SHA256)), "sha256:")
	if expectedHash != "" && expectedHash != hex.EncodeToString(hasher.Sum(nil)) {
		return "", func() {}, httpx.NewAppError(httpx.CodeConflict, "form template content hash mismatch", http.StatusConflict, nil, nil)
	}
	if err := os.Rename(file.Name(), localPath); err != nil {
		return "", func() {}, httpx.NewAppError(httpx.CodeInternal, "publish local form template failed", http.StatusInternalServerError, nil, err)
	}
	return localPath, func() {}, nil
}

// MaterializePinnedTemplate reads the original object identity, never the
// mutable form/file row. A retry must use the same bytes as the first attempt.
func (m *TemplateMaterializer) MaterializePinnedTemplate(ctx context.Context, workspaceID uuid.UUID, pinJSON json.RawMessage, outDir string) (string, func(), error) {
	if m == nil || m.Storage == nil {
		return "", func() {}, fmt.Errorf("template materializer is not configured")
	}
	var pin TemplatePin
	if err := json.Unmarshal(pinJSON, &pin); err != nil {
		return "", func() {}, err
	}
	checksum, err := hex.DecodeString(pin.SHA256)
	if err != nil || len(checksum) != sha256.Size || pin.WorkspaceID != workspaceID ||
		pin.FileID == uuid.Nil || pin.FileSize <= 0 || strings.TrimSpace(pin.ObjectKey) == "" ||
		pin.Filename == "" || pin.Filename != filepkg.SanitizeFilename(pin.Filename) {
		return "", func() {}, fmt.Errorf("invalid frozen template metadata")
	}
	reader, _, err := m.Storage.Get(ctx, pin.ObjectKey)
	if err != nil {
		return "", func() {}, err
	}
	defer reader.Close()
	inputDir := filepath.Join(outDir, "input", "templates", pin.FileID.String())
	if err := os.MkdirAll(inputDir, 0o700); err != nil {
		return "", func() {}, err
	}
	target, err := os.CreateTemp(inputDir, ".template-*")
	if err != nil {
		return "", func() {}, err
	}
	defer os.Remove(target.Name())
	hasher := sha256.New()
	n, copyErr := io.Copy(io.MultiWriter(target, hasher), reader)
	closeErr := target.Close()
	if copyErr != nil {
		return "", func() {}, copyErr
	}
	if closeErr != nil {
		return "", func() {}, closeErr
	}
	if n != pin.FileSize || hex.EncodeToString(hasher.Sum(nil)) != pin.SHA256 {
		return "", func() {}, fmt.Errorf("frozen template hash/size mismatch")
	}
	path := filepath.Join(inputDir, pin.Filename)
	if err := os.Rename(target.Name(), path); err != nil {
		return "", func() {}, err
	}
	return path, func() {}, nil
}
