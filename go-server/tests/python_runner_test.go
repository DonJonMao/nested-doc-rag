package tests

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestPythonRunnerRunStep15Success(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "predictions.json"})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions.json"), []byte("{}"), 0o644))
	exec := &fakeCommandExecutor{
		results: []*pythonpkg.ProcessResult{
			{ExitCode: 0, StdoutTail: "ran"},
			{ExitCode: 0, StdoutTail: "validated"},
		},
	}
	runner := testSubprocessRunner(exec)

	result, err := runner.RunStep15Agent(context.Background(), pythonpkg.Step15RunRequest{
		RunID:           uuid.New(),
		TargetNamespace: "target",
		OutDir:          runDir,
	})

	require.NoError(t, err)
	require.NotNil(t, result.Manifest)
	require.NotNil(t, result.Validation)
	require.Equal(t, "layered", argValue(t, exec.specs[0].Args, "--retrieval-plan"))
	require.Equal(t, "step15_compat", argValue(t, exec.specs[0].Args, "--prompt-version"))
	require.Equal(t, "4-144", argValue(t, exec.specs[0].Args, "--rows"))
	require.Contains(t, exec.specs[1].Args, "validate-artifacts")
}

func TestPythonRunnerRunStep15ProcessFailure(t *testing.T) {
	exec := &fakeCommandExecutor{
		results: []*pythonpkg.ProcessResult{{ExitCode: 9, StderrTail: "failed"}},
		errs:    []error{&pythonpkg.PythonRunError{ExitCode: 9, StderrTail: "failed"}},
	}
	runner := testSubprocessRunner(exec)

	result, err := runner.RunStep15Agent(context.Background(), pythonpkg.Step15RunRequest{TargetNamespace: "target", OutDir: t.TempDir()})

	require.Error(t, err)
	require.Equal(t, 9, result.ExitCode)
}

func TestPythonRunnerValidateArtifactsFailure(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{"predictions": "predictions.json"})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions.json"), []byte("{}"), 0o644))
	exec := &fakeCommandExecutor{
		results: []*pythonpkg.ProcessResult{
			{ExitCode: 0},
			{ExitCode: 1, StderrTail: "bad"},
		},
		errs: []error{nil, errors.New("validate failed")},
	}
	runner := testSubprocessRunner(exec)

	result, err := runner.RunStep15Agent(context.Background(), pythonpkg.Step15RunRequest{TargetNamespace: "target", OutDir: runDir})

	require.Error(t, err)
	require.NotNil(t, result.Validation)
	require.False(t, result.Validation.OK)
}

func TestPythonRunnerIngestionDisabled(t *testing.T) {
	runner := testSubprocessRunner(&fakeCommandExecutor{})
	runner.IngestCommandEnabled = false

	_, err := runner.RunKnowledgeIngestion(context.Background(), pythonpkg.IngestionRequest{OutDir: t.TempDir()})

	require.ErrorIs(t, err, pythonpkg.ErrIngestionDisabled)
}

func testSubprocessRunner(exec *fakeCommandExecutor) *pythonpkg.SubprocessPythonRunner {
	return &pythonpkg.SubprocessPythonRunner{
		Builder:                    &pythonpkg.CommandBuilder{PythonExecutable: "python", ProjectDir: "/repo", DefaultConfigPath: "config/local.yaml"},
		Process:                    exec,
		ArtifactValidationEnabled:  true,
		DefaultTimeout:             time.Hour,
		Step15DefaultRetrievalMode: "layered",
		Step15DefaultPromptVersion: "step15_compat",
		Step15DefaultRows:          "4-144",
		IngestCommandEnabled:       true,
	}
}

func argIndex(args []string, needle string) int {
	for i, arg := range args {
		if arg == needle {
			return i
		}
	}
	return -1
}

func argValue(t *testing.T, args []string, needle string) string {
	t.Helper()
	idx := argIndex(args, needle)
	require.NotEqual(t, -1, idx)
	require.Less(t, idx+1, len(args))
	return args[idx+1]
}
