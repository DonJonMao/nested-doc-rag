package tests

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestPythonProcessRunSuccess(t *testing.T) {
	runner := testProcessRunner()

	result, err := runner.Run(context.Background(), helperCommand("success"), time.Second)

	require.NoError(t, err)
	require.Equal(t, 0, result.ExitCode)
	require.Contains(t, result.StdoutTail, "stdout ok")
	require.Contains(t, result.StderrTail, "stderr ok")
}

func TestPythonProcessRunNonZero(t *testing.T) {
	runner := testProcessRunner()

	result, err := runner.Run(context.Background(), helperCommand("nonzero"), time.Second)

	require.Error(t, err)
	require.Equal(t, 7, result.ExitCode)
	var runErr *pythonpkg.PythonRunError
	require.True(t, errors.As(err, &runErr))
	require.Equal(t, 7, runErr.ExitCode)
	require.Contains(t, runErr.StderrTail, "boom")
}

func TestPythonProcessRunTimeout(t *testing.T) {
	runner := testProcessRunner()

	_, err := runner.Run(context.Background(), helperCommand("sleep"), 30*time.Millisecond)

	require.Error(t, err)
	var runErr *pythonpkg.PythonRunError
	require.True(t, errors.As(err, &runErr))
	require.True(t, runErr.Timeout)
}

func TestPythonProcessRunCancel(t *testing.T) {
	runner := testProcessRunner()
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(20 * time.Millisecond)
		cancel()
	}()

	_, err := runner.Run(ctx, helperCommand("sleep"), time.Second)

	require.Error(t, err)
	var runErr *pythonpkg.PythonRunError
	require.True(t, errors.As(err, &runErr))
	require.True(t, runErr.Canceled)
}

func TestPythonProcessTailTruncation(t *testing.T) {
	runner := &pythonpkg.ProcessRunner{Logger: zap.NewNop(), KillGracePeriod: 10 * time.Millisecond, StdoutLimit: 10, StderrLimit: 10}

	result, err := runner.Run(context.Background(), helperCommand("tail"), time.Second)

	require.NoError(t, err)
	require.Len(t, result.StdoutTail, 10)
	require.True(t, strings.HasSuffix(result.StdoutTail, "0123456789"))
	require.Len(t, result.StderrTail, 10)
	require.True(t, strings.HasSuffix(result.StderrTail, "abcdefghij"))
}

func testProcessRunner() *pythonpkg.ProcessRunner {
	return &pythonpkg.ProcessRunner{Logger: zap.NewNop(), KillGracePeriod: 10 * time.Millisecond, StdoutLimit: 1024, StderrLimit: 1024}
}

func helperCommand(mode string) pythonpkg.CommandSpec {
	return pythonpkg.CommandSpec{
		Args:         []string{os.Args[0], "-test.run=TestPythonProcessHelper", "--"},
		RedactedArgs: []string{os.Args[0], "-test.run=TestPythonProcessHelper", "--"},
		Env:          []string{"GO_WANT_HELPER_PROCESS=1", "HELPER_MODE=" + mode},
	}
}

func TestPythonProcessHelper(t *testing.T) {
	if os.Getenv("GO_WANT_HELPER_PROCESS") != "1" {
		return
	}
	switch os.Getenv("HELPER_MODE") {
	case "success":
		fmt.Fprintln(os.Stdout, "stdout ok")
		fmt.Fprintln(os.Stderr, "stderr ok")
		os.Exit(0)
	case "nonzero":
		fmt.Fprintln(os.Stderr, "boom")
		os.Exit(7)
	case "sleep":
		time.Sleep(5 * time.Second)
		os.Exit(0)
	case "tail":
		fmt.Fprint(os.Stdout, "xxxx0123456789")
		fmt.Fprint(os.Stderr, "yyyyabcdefghij")
		os.Exit(0)
	default:
		os.Exit(2)
	}
}
