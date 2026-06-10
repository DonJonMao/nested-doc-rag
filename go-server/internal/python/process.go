package python

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"sync"
	"syscall"
	"time"

	"go.uber.org/zap"
)

type CommandExecutor interface {
	Run(ctx context.Context, spec CommandSpec, timeout time.Duration) (*ProcessResult, error)
}

type Metrics interface {
	ObservePythonRun(command string, status string, duration time.Duration)
	ObservePythonProcessRunning(command string, delta float64)
	ObservePythonProcessExit(command string, exitCode int)
}

type ProcessRunner struct {
	Logger          *zap.Logger
	KillGracePeriod time.Duration
	StdoutLimit     int64
	StderrLimit     int64
	Metrics         Metrics
}

func (r *ProcessRunner) Run(ctx context.Context, spec CommandSpec, timeout time.Duration) (*ProcessResult, error) {
	if len(spec.Args) == 0 || spec.Args[0] == "" {
		return nil, fmt.Errorf("%w: missing executable", ErrInvalidCommand)
	}
	logger := r.Logger
	if logger == nil {
		logger = zap.NewNop()
	}
	runCtx := ctx
	var cancel context.CancelFunc
	if timeout > 0 {
		runCtx, cancel = context.WithTimeout(ctx, timeout)
	} else {
		runCtx, cancel = context.WithCancel(ctx)
	}
	defer cancel()

	stdout := newTailBuffer(r.StdoutLimit)
	stderr := newTailBuffer(r.StderrLimit)
	started := time.Now().UTC()
	command := commandLabel(spec.Args)
	cmd := exec.CommandContext(runCtx, spec.Args[0], spec.Args[1:]...)
	cmd.Cancel = func() error { return nil }
	if spec.Dir != "" {
		cmd.Dir = spec.Dir
	}
	cmd.Env = append(os.Environ(), spec.Env...)
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	logger.Info("starting python command", zap.Strings("args", spec.RedactedArgs), zap.String("dir", cmd.Dir))
	if r.Metrics != nil {
		r.Metrics.ObservePythonProcessRunning(command, 1)
		defer r.Metrics.ObservePythonProcessRunning(command, -1)
	}

	if err := cmd.Start(); err != nil {
		finished := time.Now().UTC()
		result := processResult(-1, stdout, stderr, started, finished)
		r.observePython(command, "failed", result)
		return result, &PythonRunError{Message: "start python command failed", ExitCode: -1, StdoutTail: result.StdoutTail, StderrTail: result.StderrTail}
	}

	done := make(chan error, 1)
	go func() {
		done <- cmd.Wait()
	}()

	var waitErr error
	select {
	case waitErr = <-done:
	case <-runCtx.Done():
		timedOut := errors.Is(runCtx.Err(), context.DeadlineExceeded)
		canceled := !timedOut
		r.terminate(cmd, logger)
		select {
		case waitErr = <-done:
		case <-time.After(r.killGracePeriod()):
			r.kill(cmd, logger)
			waitErr = <-done
		}
		finished := time.Now().UTC()
		exitCode := exitCodeFromError(waitErr)
		result := processResult(exitCode, stdout, stderr, started, finished)
		status := "canceled"
		if timedOut {
			status = "timeout"
		}
		r.observePython(command, status, result)
		return result, &PythonRunError{
			Message:    cancelMessage(timedOut, canceled),
			ExitCode:   exitCode,
			StdoutTail: result.StdoutTail,
			StderrTail: result.StderrTail,
			Timeout:    timedOut,
			Canceled:   canceled,
		}
	}

	finished := time.Now().UTC()
	exitCode := exitCodeFromError(waitErr)
	result := processResult(exitCode, stdout, stderr, started, finished)
	if waitErr != nil {
		r.observePython(command, "failed", result)
		return result, &PythonRunError{
			Message:    fmt.Sprintf("python command failed with exit code %d", exitCode),
			ExitCode:   exitCode,
			StdoutTail: result.StdoutTail,
			StderrTail: result.StderrTail,
		}
	}
	r.observePython(command, "succeeded", result)
	return result, nil
}

func (r *ProcessRunner) observePython(command string, status string, result *ProcessResult) {
	if r.Metrics == nil || result == nil {
		return
	}
	r.Metrics.ObservePythonRun(command, status, result.FinishedAt.Sub(result.StartedAt))
	r.Metrics.ObservePythonProcessExit(command, result.ExitCode)
}

func commandLabel(args []string) string {
	for _, arg := range args {
		switch arg {
		case "run-step15-agent":
			return "run_step15_agent"
		case "ingest-knowledge":
			return "ingest_knowledge"
		case "validate-artifacts":
			return "validate_artifacts"
		}
	}
	return "python"
}

func (r *ProcessRunner) killGracePeriod() time.Duration {
	if r.KillGracePeriod <= 0 {
		return 10 * time.Second
	}
	return r.KillGracePeriod
}

func (r *ProcessRunner) terminate(cmd *exec.Cmd, logger *zap.Logger) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	pid := cmd.Process.Pid
	if err := syscall.Kill(-pid, syscall.SIGTERM); err != nil {
		if err := cmd.Process.Signal(syscall.SIGTERM); err != nil {
			logger.Warn("terminate python process failed", zap.Int("pid", pid), zap.Error(err))
		}
	}
}

func (r *ProcessRunner) kill(cmd *exec.Cmd, logger *zap.Logger) {
	if cmd == nil || cmd.Process == nil {
		return
	}
	pid := cmd.Process.Pid
	if err := syscall.Kill(-pid, syscall.SIGKILL); err != nil {
		if err := cmd.Process.Kill(); err != nil {
			logger.Warn("kill python process failed", zap.Int("pid", pid), zap.Error(err))
		}
	}
}

func processResult(exitCode int, stdout *tailBuffer, stderr *tailBuffer, started time.Time, finished time.Time) *ProcessResult {
	return &ProcessResult{
		ExitCode:   exitCode,
		StdoutTail: stdout.String(),
		StderrTail: stderr.String(),
		StartedAt:  started,
		FinishedAt: finished,
		Duration:   finished.Sub(started),
	}
}

func exitCodeFromError(err error) int {
	if err == nil {
		return 0
	}
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		return exitErr.ExitCode()
	}
	return -1
}

func cancelMessage(timedOut bool, canceled bool) string {
	if timedOut {
		return "python command timed out"
	}
	if canceled {
		return "python command canceled"
	}
	return "python command stopped"
}

type tailBuffer struct {
	mu    sync.Mutex
	limit int64
	data  []byte
}

func newTailBuffer(limit int64) *tailBuffer {
	if limit <= 0 {
		limit = 1024 * 1024
	}
	return &tailBuffer{limit: limit}
}

func (b *tailBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	written := len(p)
	limit := int(b.limit)
	if limit <= 0 {
		return written, nil
	}
	if len(p) >= limit {
		b.data = append(b.data[:0], p[len(p)-limit:]...)
		return written, nil
	}
	b.data = append(b.data, p...)
	if len(b.data) > limit {
		b.data = append([]byte(nil), b.data[len(b.data)-limit:]...)
	}
	return written, nil
}

func (b *tailBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return string(bytes.TrimRight(b.data, "\x00"))
}
