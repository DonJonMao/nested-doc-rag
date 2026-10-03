package tests

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestPythonEvidenceArchiveRejectsUnsafeManifestPaths(t *testing.T) {
	for _, path := range []string{"../secret.jsonl", "/tmp/secret.jsonl", `..\secret.jsonl`, " ../secret.jsonl", "."} {
		t.Run(path, func(t *testing.T) {
			runDir := t.TempDir()
			data, err := json.Marshal(map[string]any{"run_id": "run-1", "status": "completed", "artifacts": map[string]string{artifact.TypeEvidenceProvenance: path}})
			require.NoError(t, err)
			require.NoError(t, os.WriteFile(filepath.Join(runDir, pythonpkg.RunManifestFilename), data, 0o644))
			_, err = pythonpkg.LoadRunManifestFromDir(runDir)
			require.ErrorIs(t, err, pythonpkg.ErrManifestInvalid)
		})
	}
}

func TestPythonEvidenceArchivePreflightsFilesBeforeRegistration(t *testing.T) {
	for _, mode := range []string{"missing", "symlink_escape", "directory"} {
		t.Run(mode, func(t *testing.T) {
			runDir := writeTestManifest(t, map[string]string{artifact.TypePredictions: "predictions.jsonl", artifact.TypeEvidenceProvenance: "evidence_provenance.jsonl"})
			require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions.jsonl"), []byte("{}\n"), 0o644))
			path := filepath.Join(runDir, "evidence_provenance.jsonl")
			switch mode {
			case "symlink_escape":
				outside := filepath.Join(t.TempDir(), "outside.jsonl")
				require.NoError(t, os.WriteFile(outside, []byte("private\n"), 0o644))
				require.NoError(t, os.Symlink(outside, path))
			case "directory":
				require.NoError(t, os.Mkdir(path, 0o755))
			}
			manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
			require.NoError(t, err)
			validation, err := pythonpkg.ValidateArtifactsFromManifest(runDir, manifest)
			require.NoError(t, err)
			require.False(t, validation.OK)
			require.Contains(t, validation.Missing, artifact.TypeEvidenceProvenance)
			registrar := &fakeArtifactRegistrar{}
			_, err = pythonpkg.NewArtifactArchiver(registrar, nil).ArchiveStep15Artifacts(context.Background(), uuid.New(), uuid.New(), manifest, auth.Principal{UserID: uuid.New()})
			require.ErrorIs(t, err, pythonpkg.ErrArtifactArchiveFail)
			require.Empty(t, registrar.requests, "invalid artifact sets must not partly register")
		})
	}
}

func TestPythonEvidenceArchiveRejectsManifestSymlinkEscape(t *testing.T) {
	outside := writeTestManifest(t, map[string]string{artifact.TypeEvidenceProvenance: "evidence_provenance.jsonl"})
	runDir := t.TempDir()
	require.NoError(t, os.Symlink(filepath.Join(outside, pythonpkg.RunManifestFilename), filepath.Join(runDir, pythonpkg.RunManifestFilename)))
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "evidence_provenance.jsonl"), []byte("{}\n"), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	registrar := &fakeArtifactRegistrar{}
	_, err = pythonpkg.NewArtifactArchiver(registrar, nil).ArchiveStep15Artifacts(context.Background(), uuid.New(), uuid.New(), manifest, auth.Principal{UserID: uuid.New()})
	require.ErrorIs(t, err, pythonpkg.ErrArtifactArchiveFail)
	require.Empty(t, registrar.requests)
}
