package python

import (
	"errors"
	"fmt"
)

var (
	ErrInvalidCommand      = errors.New("invalid python command")
	ErrManifestInvalid     = errors.New("run manifest invalid")
	ErrIngestionDisabled   = errors.New("ingest-knowledge command is disabled")
	ErrArtifactArchiveFail = errors.New("artifact archive failed")
)

type PythonRunError struct {
	Message    string
	ExitCode   int
	StdoutTail string
	StderrTail string
	Timeout    bool
	Canceled   bool
}

func (e *PythonRunError) Error() string {
	if e == nil {
		return ""
	}
	if e.Message != "" {
		return e.Message
	}
	if e.Timeout {
		return "python command timed out"
	}
	if e.Canceled {
		return "python command canceled"
	}
	return fmt.Sprintf("python command failed with exit code %d", e.ExitCode)
}

func (e *PythonRunError) Unwrap() error {
	return nil
}
