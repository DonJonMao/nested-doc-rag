package python

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

type pinProcess struct {
	run   func(CommandSpec)
	calls []CommandSpec
}

func (p *pinProcess) Run(_ context.Context, spec CommandSpec, _ time.Duration) (*ProcessResult, error) {
	p.calls = append(p.calls, spec)
	if p.run != nil {
		p.run(spec)
	}
	return &ProcessResult{ExitCode: 0}, nil
}

func pinnedTestScopes() []map[string]any {
	return []map[string]any{
		{"collection": "shared", "namespace": "room", "knowledge_base_id": uuid.NewString(), "index_version_id": uuid.NewString(), "storage_contract": "versioned_v1"},
		{"collection": "shared", "namespace": "global", "knowledge_base_id": uuid.NewString(), "index_version_id": uuid.NewString(), "storage_contract": "versioned_v1"},
	}
}

func pinBytes(t *testing.T, value any) []byte {
	t.Helper()
	data, err := json.Marshal(value)
	require.NoError(t, err)
	return data
}

func TestPinnedRunnerKeepsPreprocessScopeAuthorityWhenProcessRewritesFile(t *testing.T) {
	for _, tamper := range []bool{false, true} {
		t.Run(map[bool]string{false: "matching", true: "process changed pins and manifest"}[tamper], func(t *testing.T) {
			dir := t.TempDir()
			path := filepath.Join(dir, "index_scopes.json")
			scopes := pinnedTestScopes()
			require.NoError(t, os.WriteFile(path, pinBytes(t, scopes), 0600))
			process := &pinProcess{run: func(spec CommandSpec) {
				require.Contains(t, spec.Args, "--index-scopes")
				if tamper {
					scopes[0]["index_version_id"] = uuid.NewString()
					require.NoError(t, os.WriteFile(path, pinBytes(t, scopes), 0600))
				}
				manifest := map[string]any{"run_id": "test", "status": "succeeded", "artifacts": map[string]string{"predictions": "predictions.jsonl"}, "index_scopes": scopes,
					"form_input": map[string]any{"acquisition_contract": map[string]any{"index_scope_contract": map[string]any{"version": "pinned-index-scopes-v1", "scopes": scopes}}}}
				require.NoError(t, os.WriteFile(filepath.Join(dir, RunManifestFilename), pinBytes(t, manifest), 0600))
			}}
			runner := &SubprocessPythonRunner{Builder: &CommandBuilder{}, Process: process}
			_, err := runner.RunStep15Agent(context.Background(), Step15RunRequest{OutDir: dir, TargetNamespace: "room", GlobalNamespace: "global", IndexScopesPath: path})
			if tamper {
				require.ErrorContains(t, err, "index scopes differ")
			} else {
				require.NoError(t, err)
			}
			require.Len(t, process.calls, 1)
		})
	}
}

func TestPinnedRunnerRejectsEmptyMalformedAndUnboundScopesBeforeProcess(t *testing.T) {
	for _, data := range [][]byte{[]byte("null"), []byte("[]"), []byte("{}"), []byte("[{},{}]"), pinBytes(t, []map[string]any{{"namespace": "room", "collection": map[string]any{}}, {"namespace": "global", "collection": map[string]any{}}})} {
		dir := t.TempDir()
		path := filepath.Join(dir, "pins.json")
		require.NoError(t, os.WriteFile(path, data, 0600))
		process := &pinProcess{}
		runner := &SubprocessPythonRunner{Builder: &CommandBuilder{}, Process: process}
		_, err := runner.RunStep15Agent(context.Background(), Step15RunRequest{OutDir: dir, TargetNamespace: "room", GlobalNamespace: "global", IndexScopesPath: path})
		require.Error(t, err)
		require.Empty(t, process.calls)
	}
}

func TestVersionedIngestionCommandAndReceiptAreRequired(t *testing.T) {
	for _, receipt := range []string{"", "not json", `{"schema_version":"kb-index-validation-v1"}`} {
		dir := t.TempDir()
		version := uuid.NewString()
		process := &pinProcess{run: func(spec CommandSpec) {
			for _, value := range []string{"--index-version-id", version, "--input-snapshot", "input.json", "--input-snapshot-hash", "hash"} {
				require.Contains(t, spec.Args, value)
			}
			if receipt != "" {
				require.NoError(t, os.WriteFile(filepath.Join(dir, "validation_receipt.json"), []byte(receipt), 0600))
			}
		}}
		runner := &SubprocessPythonRunner{Builder: &CommandBuilder{}, Process: process, IngestCommandEnabled: true}
		result, err := runner.RunKnowledgeIngestion(context.Background(), IngestionRequest{OutDir: dir, IndexVersionID: version, InputSnapshotPath: "input.json", InputSnapshotHash: "hash"})
		if json.Valid([]byte(receipt)) {
			require.NoError(t, err)
			require.Equal(t, receipt, string(result.ValidationReceiptJSON))
		} else {
			require.Error(t, err)
		}
	}
}
