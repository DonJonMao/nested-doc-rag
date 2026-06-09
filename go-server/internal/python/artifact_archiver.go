package python

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/google/uuid"
	"go.uber.org/zap"
)

type ArtifactRegistrar interface {
	RegisterArtifact(ctx context.Context, req artifact.RegisterArtifactRequest, actor auth.Principal) (*artifact.RunArtifact, error)
}

type ArtifactArchiver struct {
	Registrar ArtifactRegistrar
	Logger    *zap.Logger
}

func NewArtifactArchiver(registrar ArtifactRegistrar, logger *zap.Logger) *ArtifactArchiver {
	if logger == nil {
		logger = zap.NewNop()
	}
	return &ArtifactArchiver{Registrar: registrar, Logger: logger}
}

func (a *ArtifactArchiver) ArchiveStep15Artifacts(ctx context.Context, workspaceID uuid.UUID, runID uuid.UUID, manifest *RunManifest, actor auth.Principal) ([]artifact.RunArtifact, error) {
	if a == nil || a.Registrar == nil {
		return nil, fmt.Errorf("%w: artifact registrar is not configured", ErrArtifactArchiveFail)
	}
	if manifest == nil {
		return nil, fmt.Errorf("%w: manifest is nil", ErrArtifactArchiveFail)
	}
	artifacts := make([]artifact.RunArtifact, 0, len(manifest.Artifacts))
	for artifactType := range manifest.Artifacts {
		path, ok := manifest.ArtifactPath(artifactType)
		if !ok || strings.TrimSpace(path) == "" {
			return nil, fmt.Errorf("%w: artifact %s path is empty", ErrArtifactArchiveFail, artifactType)
		}
		meta, err := inspectArtifact(path)
		if err != nil {
			return nil, fmt.Errorf("%w: inspect %s: %v", ErrArtifactArchiveFail, artifactType, err)
		}
		registered, err := a.Registrar.RegisterArtifact(ctx, artifact.RegisterArtifactRequest{
			WorkspaceID:  workspaceID,
			RunID:        runID,
			ArtifactType: artifactType,
			Filename:     filepath.Base(path),
			LocalPath:    path,
			ContentType:  meta.contentType,
			FileSize:     meta.size,
			SHA256:       meta.sha256,
		}, actor)
		if err != nil {
			return nil, fmt.Errorf("%w: register %s: %v", ErrArtifactArchiveFail, artifactType, err)
		}
		artifacts = append(artifacts, *registered)
	}
	return artifacts, nil
}

type artifactMeta struct {
	size        int64
	sha256      string
	contentType string
}

func inspectArtifact(path string) (artifactMeta, error) {
	file, err := os.Open(path)
	if err != nil {
		return artifactMeta{}, err
	}
	defer func() {
		_ = file.Close()
	}()
	info, err := file.Stat()
	if err != nil {
		return artifactMeta{}, err
	}
	header := make([]byte, 512)
	read, err := file.Read(header)
	if err != nil && err != io.EOF {
		return artifactMeta{}, err
	}
	contentType := http.DetectContentType(header[:read])
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return artifactMeta{}, err
	}
	hasher := sha256.New()
	if _, err := io.Copy(hasher, file); err != nil {
		return artifactMeta{}, err
	}
	return artifactMeta{size: info.Size(), sha256: hex.EncodeToString(hasher.Sum(nil)), contentType: contentType}, nil
}
