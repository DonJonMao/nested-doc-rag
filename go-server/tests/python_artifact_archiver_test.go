package tests

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestPythonArtifactArchiverRegistersAllManifestArtifacts(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "predictions.json", "filled_form": "filled.xlsx"})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions.json"), []byte("predictions"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "filled.xlsx"), []byte("filled"), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	registrar := &fakeArtifactRegistrar{}
	archiver := pythonpkg.NewArtifactArchiver(registrar, nil)
	workspaceID := uuid.New()
	runID := uuid.New()
	actor := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleOperator}}

	registered, err := archiver.ArchiveStep15Artifacts(context.Background(), workspaceID, runID, manifest, actor)

	require.NoError(t, err)
	require.Len(t, registered, 2)
	require.Len(t, registrar.requests, 2)
	require.ElementsMatch(t, []string{"predictions", "filled_form"}, []string{registrar.requests[0].ArtifactType, registrar.requests[1].ArtifactType})
	for _, req := range registrar.requests {
		require.NotEmpty(t, req.LocalPath)
		require.NotEmpty(t, req.SHA256)
		require.Greater(t, req.FileSize, int64(0))
		require.Nil(t, req.Reader)
	}
}

func TestPythonArtifactArchiverMissingFileReturnsError(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "missing.json"})
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	archiver := pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{}, nil)

	_, err = archiver.ArchiveStep15Artifacts(context.Background(), uuid.New(), uuid.New(), manifest, auth.Principal{UserID: uuid.New()})

	require.Error(t, err)
	require.ErrorIs(t, err, pythonpkg.ErrArtifactArchiveFail)
}

func TestPythonArtifactArchiverSkipsEmptyOptionalArtifacts(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "predictions.json", "filled_form": ""})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions.json"), []byte("predictions"), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	registrar := &fakeArtifactRegistrar{}
	archiver := pythonpkg.NewArtifactArchiver(registrar, nil)

	registered, err := archiver.ArchiveStep15Artifacts(context.Background(), uuid.New(), uuid.New(), manifest, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	require.Len(t, registered, 1)
	require.Len(t, registrar.requests, 1)
	require.Equal(t, "predictions", registrar.requests[0].ArtifactType)
}

func TestPythonArtifactArchiverComputesSHA256(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "predictions.json"})
	data := []byte("hash me")
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions.json"), data, 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	registrar := &fakeArtifactRegistrar{}
	archiver := pythonpkg.NewArtifactArchiver(registrar, nil)

	_, err = archiver.ArchiveStep15Artifacts(context.Background(), uuid.New(), uuid.New(), manifest, auth.Principal{UserID: uuid.New()})

	require.NoError(t, err)
	sum := sha256.Sum256(data)
	require.Equal(t, hex.EncodeToString(sum[:]), registrar.requests[0].SHA256)
}

func TestPythonArtifactArchiverRegistrarError(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "predictions.json"})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions.json"), []byte("{}"), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	archiver := pythonpkg.NewArtifactArchiver(&fakeArtifactRegistrar{err: errors.New("store failed")}, nil)

	_, err = archiver.ArchiveStep15Artifacts(context.Background(), uuid.New(), uuid.New(), manifest, auth.Principal{UserID: uuid.New()})

	require.Error(t, err)
	require.ErrorIs(t, err, pythonpkg.ErrArtifactArchiveFail)
}

type fakeArtifactRegistrar struct {
	requests []artifact.RegisterArtifactRequest
	actors   []auth.Principal
	err      error
}

func (f *fakeArtifactRegistrar) RegisterArtifact(ctx context.Context, req artifact.RegisterArtifactRequest, actor auth.Principal) (*artifact.RunArtifact, error) {
	if f.err != nil {
		return nil, f.err
	}
	f.requests = append(f.requests, req)
	f.actors = append(f.actors, actor)
	return &artifact.RunArtifact{
		ID:           uuid.New(),
		WorkspaceID:  req.WorkspaceID,
		RunID:        req.RunID,
		ArtifactType: req.ArtifactType,
		Filename:     req.Filename,
		LocalPath:    req.LocalPath,
		ContentType:  req.ContentType,
		FileSize:     req.FileSize,
		SHA256:       req.SHA256,
		CreatedBy:    actor.UserID,
	}, nil
}
