package python

import (
	"fmt"
	"sort"
	"strings"
)

type CommandBuilder struct {
	PythonExecutable  string
	ProjectDir        string
	DefaultConfigPath string
}

func (b *CommandBuilder) BuildStep15AgentCommand(req Step15RunRequest) CommandSpec {
	executable := strings.TrimSpace(b.PythonExecutable)
	if executable == "" {
		executable = "python"
	}
	configPath := strings.TrimSpace(req.ConfigPath)
	if configPath == "" {
		configPath = strings.TrimSpace(b.DefaultConfigPath)
	}
	args := []string{
		executable,
		"-m", "nested_doc_rag.cli",
		"run-step15-agent",
		"--config", configPath,
		"--target-namespace", strings.TrimSpace(req.TargetNamespace),
		"--global-namespace", strings.TrimSpace(req.GlobalNamespace),
		"--room-context", strings.TrimSpace(req.RoomContext),
		"--rows", strings.TrimSpace(req.Rows),
		"--retrieval-plan", strings.TrimSpace(req.RetrievalMode),
		"--prompt-version", strings.TrimSpace(req.PromptVersion),
		"--out-dir", strings.TrimSpace(req.OutDir),
	}
	if req.Judge {
		args = append(args, "--judge")
	} else {
		args = append(args, "--no-judge")
	}
	if req.UseJudgeCache {
		args = append(args, "--use-judge-cache")
	}
	if strings.TrimSpace(req.JudgeCachePath) != "" {
		args = append(args, "--judge-cache", strings.TrimSpace(req.JudgeCachePath))
	}
	if req.Writeback && strings.TrimSpace(req.TemplatePath) != "" {
		args = append(args, "--template", strings.TrimSpace(req.TemplatePath), "--writeback")
	}
	if req.Resume {
		args = append(args, "--resume")
	}
	return CommandSpec{
		Dir:          strings.TrimSpace(b.ProjectDir),
		Env:          envMapToList(req.Env),
		Args:         args,
		RedactedArgs: redactArgs(args),
	}
}

func (b *CommandBuilder) BuildValidateArtifactsCommand(runDir string) CommandSpec {
	executable := strings.TrimSpace(b.PythonExecutable)
	if executable == "" {
		executable = "python"
	}
	args := []string{
		executable,
		"-m", "nested_doc_rag.cli",
		"validate-artifacts",
		"--run-dir", strings.TrimSpace(runDir),
	}
	return CommandSpec{Dir: strings.TrimSpace(b.ProjectDir), Args: args, RedactedArgs: redactArgs(args)}
}

func (b *CommandBuilder) BuildKnowledgeIngestionCommand(req IngestionRequest) CommandSpec {
	executable := strings.TrimSpace(b.PythonExecutable)
	if executable == "" {
		executable = "python"
	}
	configPath := strings.TrimSpace(req.ConfigPath)
	if configPath == "" {
		configPath = strings.TrimSpace(b.DefaultConfigPath)
	}
	args := []string{
		executable,
		"-m", "nested_doc_rag.cli",
		"ingest-knowledge",
		"--config", configPath,
		"--input-dir", strings.TrimSpace(req.InputDir),
		"--namespace", strings.TrimSpace(req.Namespace),
		"--knowledge-base-id", strings.TrimSpace(req.KnowledgeBaseID),
		"--out-dir", strings.TrimSpace(req.OutDir),
	}
	if strings.TrimSpace(req.QdrantCollection) != "" {
		args = append(args, "--qdrant-collection", strings.TrimSpace(req.QdrantCollection))
	}
	if strings.TrimSpace(req.QdrantNamespace) != "" {
		args = append(args, "--qdrant-namespace", strings.TrimSpace(req.QdrantNamespace))
	}
	if req.Resume {
		args = append(args, "--resume")
	}
	return CommandSpec{
		Dir:          strings.TrimSpace(b.ProjectDir),
		Env:          envMapToList(req.Env),
		Args:         args,
		RedactedArgs: redactArgs(args),
	}
}

func envMapToList(values map[string]string) []string {
	if len(values) == 0 {
		return nil
	}
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	output := make([]string, 0, len(keys))
	for _, key := range keys {
		output = append(output, fmt.Sprintf("%s=%s", key, values[key]))
	}
	return output
}

func redactArgs(args []string) []string {
	redacted := append([]string(nil), args...)
	for i := 0; i < len(redacted); i++ {
		lower := strings.ToLower(redacted[i])
		if containsSecretName(lower) && i+1 < len(redacted) {
			redacted[i+1] = "[REDACTED]"
			i++
		}
		if strings.Contains(redacted[i], "=") {
			name, _, ok := strings.Cut(redacted[i], "=")
			if ok && containsSecretName(strings.ToLower(name)) {
				redacted[i] = name + "=[REDACTED]"
			}
		}
	}
	return redacted
}

func containsSecretName(value string) bool {
	for _, marker := range []string{"secret", "token", "password", "api_key", "apikey", "access_key", "private_key"} {
		if strings.Contains(value, marker) {
			return true
		}
	}
	return false
}
