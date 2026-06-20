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
	require.Len(t, registered, 3)
	require.Len(t, registrar.requests, 3)
	require.ElementsMatch(t, []string{"run_manifest", "predictions", "filled_form"}, artifactTypesFromRequests(registrar.requests))
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
	require.Len(t, registered, 2)
	require.Len(t, registrar.requests, 2)
	require.ElementsMatch(t, []string{"run_manifest", "predictions"}, artifactTypesFromRequests(registrar.requests))
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
	var predictionsSHA string
	for _, req := range registrar.requests {
		if req.ArtifactType == "predictions" {
			predictionsSHA = req.SHA256
		}
	}
	require.Equal(t, hex.EncodeToString(sum[:]), predictionsSHA)
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

func artifactTypesFromRequests(requests []artifact.RegisterArtifactRequest) []string {
	out := make([]string, 0, len(requests))
	for _, req := range requests {
		out = append(out, req.ArtifactType)
	}
	return out
}

func artifactTypesFromRunArtifacts(items []artifact.RunArtifact) []string {
	out := make([]string, 0, len(items))
	for _, item := range items {
		out = append(out, item.ArtifactType)
	}
	return out
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
