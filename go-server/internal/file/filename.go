package file

import (
	"path/filepath"
	"strings"
	"unicode"
)

const maxFilenameRunes = 180

func SanitizeFilename(name string) string {
	name = filepath.Base(strings.ReplaceAll(name, "\\", "/"))
	var b strings.Builder
	for _, r := range name {
		if unicode.IsControl(r) {
			continue
		}
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '.' || r == '_' || r == '-' || r == ' ' {
			b.WriteRune(r)
		}
	}
	output := strings.TrimSpace(b.String())
	if output == "" || output == "." || output == ".." {
		output = "uploaded_file"
	}
	runes := []rune(output)
	if len(runes) > maxFilenameRunes {
		output = string(runes[:maxFilenameRunes])
	}
	return output
}
