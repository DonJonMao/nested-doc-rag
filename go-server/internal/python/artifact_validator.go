package python

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func ValidateArtifactsFromManifest(runDir string, manifest *RunManifest) (*ArtifactValidationResult, error) {
	result := &ArtifactValidationResult{RunDir: runDir, OK: true}
	if strings.TrimSpace(runDir) == "" {
		result.OK = false
		result.Errors = append(result.Errors, "run_dir is required")
		return result, nil
	}
	if _, err := os.Stat(filepath.Join(runDir, RunManifestFilename)); err != nil {
		result.OK = false
		result.Missing = append(result.Missing, RunManifestFilename)
	}
	if manifest == nil {
		result.OK = false
		result.Errors = append(result.Errors, "manifest is nil")
		return result, nil
	}
	if manifest.runDir == "" {
		manifestCopy := *manifest
		manifestCopy.runDir = runDir
		manifest = &manifestCopy
	}
	for name, path := range manifest.Artifacts {
		if strings.TrimSpace(path) == "" {
			result.OK = false
			result.Missing = append(result.Missing, name)
			continue
		}
		resolved, ok := manifest.ArtifactPath(name)
		if !ok {
			resolved = path
		}
		if _, err := os.Stat(resolved); err != nil {
			result.OK = false
			result.Missing = append(result.Missing, name)
		}
	}
	return result, nil
}

type ArtifactValidator struct {
	Builder *CommandBuilder
	Process CommandExecutor
	Timeout time.Duration
}

func (v *ArtifactValidator) Validate(ctx context.Context, runDir string) (*ArtifactValidationResult, error) {
	if v == nil || v.Builder == nil || v.Process == nil {
		return nil, fmt.Errorf("%w: artifact validator is not configured", ErrInvalidCommand)
	}
	spec := v.Builder.BuildValidateArtifactsCommand(runDir)
	result, err := v.Process.Run(ctx, spec, v.Timeout)
	rawOutput := ""
	if result != nil {
		rawOutput = strings.TrimSpace(result.StdoutTail + "\n" + result.StderrTail)
	}
	validation := &ArtifactValidationResult{RunDir: runDir, OK: err == nil, RawOutput: rawOutput}
	if err != nil {
		validation.Errors = append(validation.Errors, err.Error())
		return validation, err
	}
	return validation, nil
}
