package tests

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/stretchr/testify/require"
)

func TestPythonGoLevelArtifactValidatorValidManifest(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "predictions.json", "trace_summary": "trace.json"})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions.json"), []byte("{}"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "trace.json"), []byte("{}"), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)

	result, err := pythonpkg.ValidateArtifactsFromManifest(runDir, manifest)

	require.NoError(t, err)
	require.True(t, result.OK)
	require.Empty(t, result.Missing)
}

func TestPythonGoLevelArtifactValidatorMissingArtifact(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "missing.json"})
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)

	result, err := pythonpkg.ValidateArtifactsFromManifest(runDir, manifest)

	require.NoError(t, err)
	require.False(t, result.OK)
	require.Contains(t, result.Missing, "predictions")
}

func TestPythonArtifactValidatorCLISuccess(t *testing.T) {
	exec := &fakeCommandExecutor{results: []*pythonpkg.ProcessResult{{ExitCode: 0, StdoutTail: "ok"}}}
	validator := pythonpkg.ArtifactValidator{
		Builder: &pythonpkg.CommandBuilder{PythonExecutable: "python", ProjectDir: "/repo"},
		Process: exec,
	}

	result, err := validator.Validate(context.Background(), "/tmp/run")

	require.NoError(t, err)
	require.True(t, result.OK)
	require.Contains(t, result.RawOutput, "ok")
	require.Contains(t, exec.specs[0].Args, "validate-artifacts")
}

func TestPythonArtifactValidatorCLIFailure(t *testing.T) {
	exec := &fakeCommandExecutor{
		results: []*pythonpkg.ProcessResult{{ExitCode: 1, StderrTail: "bad"}},
		errs:    []error{errors.New("validator failed")},
	}
	validator := pythonpkg.ArtifactValidator{
		Builder: &pythonpkg.CommandBuilder{PythonExecutable: "python", ProjectDir: "/repo"},
		Process: exec,
	}

	result, err := validator.Validate(context.Background(), "/tmp/run")

	require.Error(t, err)
	require.False(t, result.OK)
	require.Contains(t, result.Errors[0], "validator failed")
	require.Contains(t, result.RawOutput, "bad")
}
