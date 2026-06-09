package tests

import (
	"os"
	"path/filepath"
	"testing"

	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/stretchr/testify/require"
)

func TestPythonLoadRunManifest(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "predictions.json"})

	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)

	require.NoError(t, err)
	require.Equal(t, "run-1", manifest.RunID)
	require.Equal(t, "succeeded", manifest.Status)
	require.Equal(t, "target", manifest.TargetNamespace)
}

func TestPythonManifestRelativeArtifactPathResolved(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "nested/predictions.json"})

	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	path, ok := manifest.ArtifactPath("predictions")

	require.True(t, ok)
	require.Equal(t, filepath.Join(runDir, "nested", "predictions.json"), path)
}

func TestPythonManifestMissingReturnsError(t *testing.T) {
	_, err := pythonpkg.LoadRunManifestFromDir(t.TempDir())

	require.Error(t, err)
}

func TestPythonManifestMalformedReturnsError(t *testing.T) {
	runDir := t.TempDir()
	require.NoError(t, os.WriteFile(filepath.Join(runDir, pythonpkg.RunManifestFilename), []byte("{"), 0o644))

	_, err := pythonpkg.LoadRunManifestFromDir(runDir)

	require.Error(t, err)
}

func writeTestManifest(t *testing.T, artifacts map[string]string) string {
	t.Helper()
	runDir := t.TempDir()
	data := `{
		"run_id": "run-1",
		"created_at": "2026-01-01T00:00:00Z",
		"finished_at": "2026-01-01T00:01:00Z",
		"status": "succeeded",
		"engine": "step15",
		"target_namespace": "target",
		"room_context": "room",
		"rows": "4-144",
		"judge_enabled": false,
		"writeback_enabled": true,
		"artifacts": {`
	i := 0
	for key, value := range artifacts {
		if i > 0 {
			data += ","
		}
		data += `"` + key + `":"` + value + `"`
		i++
	}
	data += `},
		"counts": {"total_fields": 1, "answered": 1}
	}`
	require.NoError(t, os.WriteFile(filepath.Join(runDir, pythonpkg.RunManifestFilename), []byte(data), 0o644))
	return runDir
}
