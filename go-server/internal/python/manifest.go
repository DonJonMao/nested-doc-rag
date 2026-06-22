package python

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const RunManifestFilename = "run_manifest.json"

type RunManifest struct {
	SchemaVersion    string            `json:"schema_version"`
	RunID            string            `json:"run_id"`
	CreatedAt        string            `json:"created_at"`
	FinishedAt       string            `json:"finished_at"`
	Status           string            `json:"status"`
	Engine           string            `json:"engine"`
	TargetNamespace  string            `json:"target_namespace"`
	RoomContext      string            `json:"room_context"`
	Rows             string            `json:"rows"`
	JudgeEnabled     bool              `json:"judge_enabled"`
	WritebackEnabled bool              `json:"writeback_enabled"`
	Artifacts        map[string]string `json:"artifacts"`
	Counts           ManifestCounts    `json:"counts"`
	Writeback        ManifestWriteback `json:"writeback"`
	runDir           string
}

type ManifestCounts struct {
	TotalFields        int `json:"total_fields"`
	Answered           int `json:"answered"`
	PartialClue        int `json:"partial_clue"`
	NotFound           int `json:"not_found"`
	ConflictUnresolved int `json:"conflict_unresolved"`
	ReviewRequired     int `json:"review_required"`
	WritebackAllowed   int `json:"writeback_allowed"`
	Failed             int `json:"failed"`
}

type ManifestWriteback struct {
	Summary ManifestWritebackSummary `json:"summary"`
	Fields  []ManifestWritebackField `json:"fields"`
}

type ManifestWritebackSummary struct {
	Confirmed int `json:"confirmed"`
	Uncertain int `json:"uncertain"`
	Flagged   int `json:"flagged"`
	Written   int `json:"written"`
	Review    int `json:"review"`
}

type ManifestWritebackField struct {
	FieldKey        string           `json:"field_key"`
	FieldID         string           `json:"field_id"`
	RowIndex        int              `json:"row_index"`
	TargetCell      string           `json:"target_cell"`
	SheetName       string           `json:"sheet_name"`
	Cell            string           `json:"cell"`
	Status          string           `json:"status"`
	AnswerStatus    string           `json:"answer_status"`
	AnswerValue     any              `json:"answer_value"`
	WritebackAction string           `json:"writeback_action"`
	EvidenceRefs    []map[string]any `json:"evidence_refs"`
	ErrorCode       string           `json:"error_code,omitempty"`
}

func LoadRunManifest(path string) (*RunManifest, error) {
	if strings.TrimSpace(path) == "" {
		return nil, fmt.Errorf("%w: path is required", ErrManifestInvalid)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read run manifest: %w", err)
	}
	var manifest RunManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return nil, fmt.Errorf("parse run manifest: %w", err)
	}
	manifest.runDir = filepath.Dir(path)
	if err := manifest.Validate(); err != nil {
		return nil, err
	}
	return &manifest, nil
}

func LoadRunManifestFromDir(runDir string) (*RunManifest, error) {
	if strings.TrimSpace(runDir) == "" {
		return nil, fmt.Errorf("%w: run_dir is required", ErrManifestInvalid)
	}
	return LoadRunManifest(filepath.Join(runDir, RunManifestFilename))
}

func (m *RunManifest) ArtifactPath(name string) (string, bool) {
	if m == nil || m.Artifacts == nil {
		return "", false
	}
	value, ok := m.Artifacts[name]
	if !ok || strings.TrimSpace(value) == "" {
		return "", false
	}
	value = strings.TrimSpace(value)
	if filepath.IsAbs(value) || m.runDir == "" {
		return value, true
	}
	return filepath.Join(m.runDir, value), true
}

func (m *RunManifest) Validate() error {
	if m == nil {
		return fmt.Errorf("%w: manifest is nil", ErrManifestInvalid)
	}
	if strings.TrimSpace(m.RunID) == "" {
		return fmt.Errorf("%w: run_id is required", ErrManifestInvalid)
	}
	if strings.TrimSpace(m.Status) == "" {
		return fmt.Errorf("%w: status is required", ErrManifestInvalid)
	}
	if len(m.Artifacts) == 0 {
		return fmt.Errorf("%w: artifacts is required", ErrManifestInvalid)
	}
	return nil
}
