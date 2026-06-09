package tests

import (
	"strings"
	"testing"

	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/stretchr/testify/require"
)

func TestPythonCommandBuilderStep15FullArgs(t *testing.T) {
	builder := pythonpkg.CommandBuilder{PythonExecutable: "python3", ProjectDir: "/repo", DefaultConfigPath: "config/local.yaml"}

	spec := builder.BuildStep15AgentCommand(pythonpkg.Step15RunRequest{
		ConfigPath:      "config/prod.yaml",
		TargetNamespace: "target",
		GlobalNamespace: "global",
		RoomContext:     "room",
		Rows:            "4-144",
		RetrievalMode:   "layered",
		PromptVersion:   "step15_compat",
		Judge:           true,
		UseJudgeCache:   true,
		JudgeCachePath:  "/tmp/judge.json",
		TemplatePath:    "/tmp/template.xlsx",
		Writeback:       true,
		Resume:          true,
		OutDir:          "/tmp/out",
		Env:             map[string]string{"OPENAI_API_KEY": "secret"},
	})

	require.Equal(t, "/repo", spec.Dir)
	require.Equal(t, "python3", spec.Args[0])
	require.Contains(t, spec.Args, "run-step15-agent")
	require.Contains(t, spec.Args, "--target-namespace")
	require.Contains(t, spec.Args, "target")
	require.Contains(t, spec.Args, "--judge")
	require.Contains(t, spec.Args, "--use-judge-cache")
	require.Contains(t, spec.Args, "--judge-cache")
	require.Contains(t, spec.Args, "--template")
	require.Contains(t, spec.Args, "--writeback")
	require.Contains(t, spec.Args, "--resume")
	require.NotContains(t, strings.Join(spec.RedactedArgs, " "), "secret")
	require.Contains(t, spec.Env, "OPENAI_API_KEY=secret")
}

func TestPythonCommandBuilderNoJudgeAndNoWritebackTemplate(t *testing.T) {
	builder := pythonpkg.CommandBuilder{PythonExecutable: "python", ProjectDir: "/repo", DefaultConfigPath: "config/local.yaml"}

	spec := builder.BuildStep15AgentCommand(pythonpkg.Step15RunRequest{
		TargetNamespace: "target",
		Rows:            "4-5",
		RetrievalMode:   "flat",
		PromptVersion:   "v1",
		TemplatePath:    "/tmp/template.xlsx",
		Writeback:       false,
		OutDir:          "/tmp/out",
	})

	require.Contains(t, spec.Args, "--no-judge")
	require.NotContains(t, spec.Args, "--writeback")
	require.NotContains(t, spec.Args, "--template")
	require.Contains(t, spec.Args, "config/local.yaml")
}

func TestPythonCommandBuilderValidateArtifacts(t *testing.T) {
	builder := pythonpkg.CommandBuilder{PythonExecutable: "python", ProjectDir: "/repo"}

	spec := builder.BuildValidateArtifactsCommand("/tmp/run")

	require.Equal(t, []string{"python", "-m", "nested_doc_rag.cli", "validate-artifacts", "--run-dir", "/tmp/run"}, spec.Args)
}

func TestPythonCommandBuilderIngest(t *testing.T) {
	builder := pythonpkg.CommandBuilder{PythonExecutable: "python", ProjectDir: "/repo", DefaultConfigPath: "config/local.yaml"}

	spec := builder.BuildKnowledgeIngestionCommand(pythonpkg.IngestionRequest{
		InputDir:        "/data/input",
		Namespace:       "kb_ns",
		KnowledgeBaseID: "kb-1",
		OutDir:          "/tmp/ingest",
		Resume:          true,
	})

	require.Contains(t, spec.Args, "ingest-knowledge")
	require.Contains(t, spec.Args, "--input-dir")
	require.Contains(t, spec.Args, "/data/input")
	require.Contains(t, spec.Args, "--knowledge-base-id")
	require.Contains(t, spec.Args, "kb-1")
	require.Contains(t, spec.Args, "--resume")
}
