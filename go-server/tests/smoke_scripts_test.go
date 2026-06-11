package tests

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSmokeScriptsExistAndExecutable(t *testing.T) {
	for _, name := range []string{"smoke_api.sh", "smoke_auth.sh", "smoke_files.sh", "smoke_jobs.sh"} {
		path := filepath.Join("..", "scripts", name)
		info, err := os.Stat(path)
		require.NoError(t, err)
		require.False(t, info.IsDir())
		require.NotZero(t, info.Mode()&0o111, "%s should be executable", name)
		content, err := os.ReadFile(path)
		require.NoError(t, err)
		text := string(content)
		require.Contains(t, text, "set -euo pipefail")
		require.NotContains(t, text, "sk-")
		require.NotContains(t, text, "ChangeMe123")
		require.NotContains(t, strings.ToLower(text), "real-token")
		require.NotContains(t, strings.ToLower(text), "api_key=")
	}
}
