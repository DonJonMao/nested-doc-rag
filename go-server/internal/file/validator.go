package file

import (
	"net/http"
	"path/filepath"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
)

type Validator struct {
	MaxUploadSize     int64
	AllowedExtensions map[string]struct{}
	AllowedMIMETypes  map[string]struct{}
}

func NewValidator(maxUploadSize int64, extensions []string, mimeTypes []string) *Validator {
	extSet := make(map[string]struct{}, len(extensions))
	for _, ext := range extensions {
		ext = strings.ToLower(strings.TrimSpace(ext))
		if ext != "" {
			extSet[ext] = struct{}{}
		}
	}
	mimeSet := make(map[string]struct{}, len(mimeTypes))
	for _, mime := range mimeTypes {
		mime = strings.ToLower(strings.TrimSpace(mime))
		if mime != "" {
			mimeSet[mime] = struct{}{}
		}
	}
	return &Validator{MaxUploadSize: maxUploadSize, AllowedExtensions: extSet, AllowedMIMETypes: mimeSet}
}

func (v *Validator) ValidateFilename(filename string) error {
	if strings.TrimSpace(filename) == "" {
		return invalid("filename is required", nil)
	}
	normalized := strings.ReplaceAll(filename, "\\", "/")
	if strings.HasPrefix(normalized, "/") || strings.Contains(normalized, "../") || strings.Contains(normalized, "/..") || normalized == "." || normalized == ".." {
		return invalid("filename must not contain path traversal", nil)
	}
	return nil
}

func (v *Validator) ValidateSize(size int64) error {
	if size < 0 {
		return invalid("file size is invalid", nil)
	}
	if v.MaxUploadSize > 0 && size > v.MaxUploadSize {
		return invalid("file is too large", map[string]any{"max_upload_size": v.MaxUploadSize})
	}
	return nil
}

func (v *Validator) ValidateExtension(filename string) error {
	ext := strings.ToLower(filepath.Ext(filename))
	if ext == "" {
		return invalid("file extension is required", nil)
	}
	if _, ok := v.AllowedExtensions[ext]; !ok {
		return invalid("file extension is not allowed", map[string]any{"extension": ext})
	}
	return nil
}

func (v *Validator) ValidateMIME(mimeType string, filename string) error {
	mimeType = strings.ToLower(strings.TrimSpace(strings.Split(mimeType, ";")[0]))
	if mimeType == "" || mimeType == "application/octet-stream" {
		return nil
	}
	if isOfficeZipMIME(mimeType, filename) {
		return nil
	}
	if _, ok := v.AllowedMIMETypes[mimeType]; !ok {
		return invalid("mime type is not allowed", map[string]any{"mime_type": mimeType})
	}
	return nil
}

func (v *Validator) ValidateUpload(filename string, size int64, mimeType string) error {
	if err := v.ValidateFilename(filename); err != nil {
		return err
	}
	if err := v.ValidateSize(size); err != nil {
		return err
	}
	if err := v.ValidateExtension(filename); err != nil {
		return err
	}
	return v.ValidateMIME(mimeType, filename)
}

func isOfficeZipMIME(mimeType string, filename string) bool {
	if mimeType != "application/zip" && mimeType != "application/x-zip-compressed" {
		return false
	}
	switch strings.ToLower(filepath.Ext(filename)) {
	case ".xlsx", ".xlsm", ".docx":
		return true
	default:
		return false
	}
}

func invalid(message string, details any) error {
	return httpx.NewAppError(httpx.CodeInvalidArgument, message, http.StatusBadRequest, details, nil)
}
