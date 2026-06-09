package python

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"
	"time"
)

type Runner interface {
	RunStep15Agent(ctx context.Context, req Step15RunRequest) (*Step15RunResult, error)
	RunKnowledgeIngestion(ctx context.Context, req IngestionRequest) (*IngestionResult, error)
	ValidateArtifacts(ctx context.Context, runDir string) (*ArtifactValidationResult, error)
}

type SubprocessPythonRunner struct {
	Builder                    *CommandBuilder
	Process                    CommandExecutor
	ArtifactValidationEnabled  bool
	DefaultTimeout             time.Duration
	Step15DefaultRetrievalMode string
	Step15DefaultPromptVersion string
	Step15DefaultRows          string
	IngestCommandEnabled       bool
}

func (r *SubprocessPythonRunner) RunStep15Agent(ctx context.Context, req Step15RunRequest) (*Step15RunResult, error) {
	if err := r.validateConfigured(); err != nil {
		return nil, err
	}
	req = r.applyStep15Defaults(req)
	if strings.TrimSpace(req.OutDir) == "" {
		return nil, fmt.Errorf("%w: out_dir is required", ErrInvalidCommand)
	}
	spec := r.Builder.BuildStep15AgentCommand(req)
	processResult, err := r.Process.Run(ctx, spec, req.Timeout)
	if err != nil {
		return processStep15Result(req, processResult, nil, nil), err
	}
	manifest, err := LoadRunManifestFromDir(req.OutDir)
	if err != nil {
		return processStep15Result(req, processResult, nil, nil), err
	}
	var validation *ArtifactValidationResult
	if r.ArtifactValidationEnabled {
		validation, err = r.ValidateArtifacts(ctx, req.OutDir)
		if err != nil {
			return processStep15Result(req, processResult, manifest, validation), err
		}
	}
	return processStep15Result(req, processResult, manifest, validation), nil
}

func (r *SubprocessPythonRunner) RunKnowledgeIngestion(ctx context.Context, req IngestionRequest) (*IngestionResult, error) {
	if err := r.validateConfigured(); err != nil {
		return nil, err
	}
	if !r.IngestCommandEnabled {
		return nil, ErrIngestionDisabled
	}
	if strings.TrimSpace(req.OutDir) == "" {
		return nil, fmt.Errorf("%w: out_dir is required", ErrInvalidCommand)
	}
	if req.Timeout <= 0 {
		req.Timeout = r.DefaultTimeout
	}
	spec := r.Builder.BuildKnowledgeIngestionCommand(req)
	processResult, err := r.Process.Run(ctx, spec, req.Timeout)
	result := &IngestionResult{IngestionID: req.IngestionID, OutDir: req.OutDir}
	if processResult != nil {
		result.StdoutTail = processResult.StdoutTail
		result.StderrTail = processResult.StderrTail
		result.ExitCode = processResult.ExitCode
		result.StartedAt = processResult.StartedAt
		result.FinishedAt = processResult.FinishedAt
	}
	result.ManifestPath = filepath.Join(req.OutDir, RunManifestFilename)
	if err != nil {
		return result, err
	}
	return result, nil
}

func (r *SubprocessPythonRunner) ValidateArtifacts(ctx context.Context, runDir string) (*ArtifactValidationResult, error) {
	if err := r.validateConfigured(); err != nil {
		return nil, err
	}
	validator := ArtifactValidator{Builder: r.Builder, Process: r.Process, Timeout: r.DefaultTimeout}
	result, err := validator.Validate(ctx, runDir)
	if err != nil {
		return result, err
	}
	manifest, err := LoadRunManifestFromDir(runDir)
	if err != nil {
		if result == nil {
			result = &ArtifactValidationResult{RunDir: runDir}
		}
		result.OK = false
		result.Errors = append(result.Errors, err.Error())
		return result, err
	}
	local, err := ValidateArtifactsFromManifest(runDir, manifest)
	if err != nil {
		return result, err
	}
	if local != nil && !local.OK {
		result.OK = false
		result.Missing = append(result.Missing, local.Missing...)
		result.Errors = append(result.Errors, local.Errors...)
		return result, fmt.Errorf("%w: manifest artifact files missing", ErrManifestInvalid)
	}
	return result, nil
}

func (r *SubprocessPythonRunner) validateConfigured() error {
	if r == nil || r.Builder == nil || r.Process == nil {
		return fmt.Errorf("%w: python runner is not configured", ErrInvalidCommand)
	}
	return nil
}

func (r *SubprocessPythonRunner) applyStep15Defaults(req Step15RunRequest) Step15RunRequest {
	if strings.TrimSpace(req.ConfigPath) == "" {
		req.ConfigPath = r.Builder.DefaultConfigPath
	}
	if strings.TrimSpace(req.RetrievalMode) == "" {
		req.RetrievalMode = r.Step15DefaultRetrievalMode
	}
	if strings.TrimSpace(req.PromptVersion) == "" {
		req.PromptVersion = r.Step15DefaultPromptVersion
	}
	if strings.TrimSpace(req.Rows) == "" {
		req.Rows = r.Step15DefaultRows
	}
	if req.Timeout <= 0 {
		req.Timeout = r.DefaultTimeout
	}
	return req
}

func processStep15Result(req Step15RunRequest, processResult *ProcessResult, manifest *RunManifest, validation *ArtifactValidationResult) *Step15RunResult {
	result := &Step15RunResult{
		RunID:      req.RunID,
		OutDir:     req.OutDir,
		Manifest:   manifest,
		Validation: validation,
	}
	if processResult != nil {
		result.StdoutTail = processResult.StdoutTail
		result.StderrTail = processResult.StderrTail
		result.ExitCode = processResult.ExitCode
		result.StartedAt = processResult.StartedAt
		result.FinishedAt = processResult.FinishedAt
	}
	return result
}
