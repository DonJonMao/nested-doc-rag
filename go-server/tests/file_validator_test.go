package tests

import (
	"testing"

	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/stretchr/testify/require"
)

func TestFileValidatorAcceptsAllowedTypes(t *testing.T) {
	validator := testFileValidator()
	for _, item := range []struct {
		name string
		mime string
	}{
		{"test.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
		{"test.xlsm", "application/vnd.ms-excel.sheet.macroEnabled.12"},
		{"test.xlsm", "application/zip"},
		{"test.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
		{"test.txt", "text/plain"},
		{"test.md", "text/markdown"},
		{"test.csv", "text/csv"},
		{"test.png", "image/png"},
		{"test.jpeg", "image/jpeg"},
	} {
		require.NoError(t, validator.ValidateUpload(item.name, 128, item.mime))
	}
}

func TestFileValidatorRejectsInvalidExtension(t *testing.T) {
	err := testFileValidator().ValidateUpload("script.sh", 128, "application/octet-stream")
	require.Error(t, err)
}

func TestFileValidatorRejectsPathTraversal(t *testing.T) {
	err := testFileValidator().ValidateUpload("../test.xlsx", 128, "application/octet-stream")
	require.Error(t, err)
}

func TestFileValidatorRejectsTooLarge(t *testing.T) {
	err := testFileValidator().ValidateUpload("test.xlsx", 2048, "application/octet-stream")
	require.Error(t, err)
}

func TestFileValidatorAllowsOctetStreamWithValidExtension(t *testing.T) {
	err := testFileValidator().ValidateUpload("test.xlsx", 128, "application/octet-stream")
	require.NoError(t, err)
}

func TestFileValidatorRejectsBadMIME(t *testing.T) {
	err := testFileValidator().ValidateUpload("test.xlsx", 128, "text/html")
	require.Error(t, err)
}

func testFileValidator() *filepkg.Validator {
	return filepkg.NewValidator(
		1024,
		[]string{".xlsx", ".xlsm", ".docx", ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg"},
		[]string{
			"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
			"application/vnd.ms-excel.sheet.macroEnabled.12",
			"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
			"text/plain",
			"text/markdown",
			"text/csv",
			"image/png",
			"image/jpeg",
			"application/octet-stream",
		},
	)
}
