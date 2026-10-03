package python

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"time"

	"github.com/google/uuid"
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
	var expectedPins []byte
	if req.IndexScopesPath != "" {
		data, err := os.ReadFile(req.IndexScopesPath)
		if err != nil {
			return nil, err
		}
		expectedPins = data
		var scopes []map[string]any
		if json.Unmarshal(data, &scopes) != nil || len(scopes) != 2 {
			return nil, fmt.Errorf("%w: invalid frozen target/global index scopes", ErrInvalidCommand)
		}
		for _, scope := range scopes {
			for _, key := range []string{"collection", "namespace", "knowledge_base_id", "index_version_id", "storage_contract"} {
				if value, ok := scope[key].(string); !ok || strings.TrimSpace(value) == "" {
					return nil, fmt.Errorf("%w: invalid frozen index scope %s", ErrInvalidCommand, key)
				}
			}
			for _, key := range []string{"knowledge_base_id", "index_version_id"} {
				if id, err := uuid.Parse(scope[key].(string)); err != nil || id == uuid.Nil {
					return nil, fmt.Errorf("%w: invalid frozen index scope %s", ErrInvalidCommand, key)
				}
			}
			if scope["storage_contract"] != "versioned_v1" && scope["storage_contract"] != "legacy_unversioned" {
				return nil, fmt.Errorf("%w: invalid frozen index storage contract", ErrInvalidCommand)
			}
		}
		if scopes[0]["namespace"] != req.TargetNamespace || scopes[1]["namespace"] != req.GlobalNamespace ||
			scopes[0]["namespace"] == scopes[1]["namespace"] || scopes[0]["collection"] != scopes[1]["collection"] ||
			(req.QdrantCollection != "" && scopes[0]["collection"] != req.QdrantCollection) {
			return nil, fmt.Errorf("%w: frozen index scopes do not match the task", ErrInvalidCommand)
		}
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
	if req.IndexScopesPath != "" {
		if err := validateRunPins(expectedPins, manifest); err != nil {
			return processStep15Result(req, processResult, manifest, nil), err
		}
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
	if req.IndexVersionID == "" && (req.InputSnapshotPath != "" || req.InputSnapshotHash != "") {
		return nil, fmt.Errorf("%w: a build snapshot requires its candidate UUID", ErrInvalidCommand)
	}
	if strings.TrimSpace(req.OutDir) == "" {
		return nil, fmt.Errorf("%w: out_dir is required", ErrInvalidCommand)
	}
	if req.Timeout <= 0 {
		req.Timeout = r.DefaultTimeout
	}
	if req.IndexVersionID != "" && (req.InputSnapshotPath == "" || req.InputSnapshotHash == "") {
		return nil, fmt.Errorf("%w: versioned ingestion requires its frozen input snapshot and hash", ErrInvalidCommand)
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
	if req.IndexVersionID != "" {
		result.ValidationReceiptPath = filepath.Join(req.OutDir, "validation_receipt.json")
		data, err := os.ReadFile(result.ValidationReceiptPath)
		if err != nil || !json.Valid(data) {
			return result, fmt.Errorf("%w: versioned ingestion validation receipt missing or invalid", ErrManifestInvalid)
		}
		result.ValidationReceiptJSON = append(json.RawMessage(nil), data...)
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

func validateRunPins(data []byte, manifest *RunManifest) error {
	var expected, actual any
	if json.Unmarshal(data, &expected) != nil || json.Unmarshal(manifest.IndexScopes, &actual) != nil || !reflect.DeepEqual(expected, actual) {
		return fmt.Errorf("%w: result index scopes differ from the frozen task", ErrManifestInvalid)
	}
	var snapshot struct {
		Acquisition struct {
			Contract struct {
				Version string `json:"version"`
				Scopes  any    `json:"scopes"`
			} `json:"index_scope_contract"`
		} `json:"acquisition_contract"`
	}
	if json.Unmarshal(manifest.FormInput, &snapshot) != nil || snapshot.Acquisition.Contract.Version != "pinned-index-scopes-v1" || !reflect.DeepEqual(expected, snapshot.Acquisition.Contract.Scopes) {
		return fmt.Errorf("%w: form input scope contract differs from the frozen task", ErrManifestInvalid)
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
