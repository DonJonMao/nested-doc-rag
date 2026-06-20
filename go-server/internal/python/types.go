package python

import (
	"time"

	"github.com/google/uuid"
)

type Step15RunRequest struct {
	WorkspaceID uuid.UUID
	JobID       uuid.UUID
	RunID       uuid.UUID

	ConfigPath      string
	TargetNamespace string
	GlobalNamespace string
	RoomContext     string
	Rows            string
	RetrievalMode   string
	PromptVersion   string
	Judge           bool
	UseJudgeCache   bool
	JudgeCachePath  string
	TemplatePath    string
	Writeback       bool
	Resume          bool
	OutDir          string
	Timeout         time.Duration
	Env             map[string]string
}

type Step15RunResult struct {
	RunID      uuid.UUID
	OutDir     string
	Manifest   *RunManifest
	Validation *ArtifactValidationResult
	StdoutTail string
	StderrTail string
	ExitCode   int
	StartedAt  time.Time
	FinishedAt time.Time
}

type IngestionRequest struct {
	WorkspaceID uuid.UUID
	JobID       uuid.UUID
	IngestionID uuid.UUID

	ConfigPath       string
	InputDir         string
	Namespace        string
	KnowledgeBaseID  string
	QdrantCollection string
	QdrantNamespace  string
	OutDir           string
	Resume           bool
	Timeout          time.Duration
	Env              map[string]string
}

type IngestionResult struct {
	IngestionID  uuid.UUID
	OutDir       string
	ManifestPath string
	StdoutTail   string
	StderrTail   string
	ExitCode     int
	StartedAt    time.Time
	FinishedAt   time.Time
}

type ArtifactValidationResult struct {
	RunDir    string
	OK        bool
	Missing   []string
	Errors    []string
	RawOutput string
}

type CommandSpec struct {
	Dir          string
	Env          []string
	Args         []string
	RedactedArgs []string
}

type ProcessResult struct {
	ExitCode   int
	StdoutTail string
	StderrTail string
	StartedAt  time.Time
	FinishedAt time.Time
	Duration   time.Duration
}
