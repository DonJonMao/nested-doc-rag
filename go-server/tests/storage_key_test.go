package tests

import (
	"strings"
	"testing"

	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestBuildFileObjectKeyCategoryPaths(t *testing.T) {
	workspaceID := uuid.New()
	fileID := uuid.New()

	require.Contains(t, filepkg.BuildFileObjectKey(workspaceID, fileID, filepkg.FileCategoryKnowledgeDocument, "知识.xlsx"), "/documents/")
	require.Contains(t, filepkg.BuildFileObjectKey(workspaceID, fileID, filepkg.FileCategoryFormTemplate, "表.xlsx"), "/forms/")
	require.Contains(t, filepkg.BuildFileObjectKey(workspaceID, fileID, filepkg.FileCategoryProofAttachment, "证明.png"), "/attachments/")
	require.Contains(t, filepkg.BuildFileObjectKey(workspaceID, fileID, filepkg.FileCategoryMisc, "misc.docx"), "/files/")
}

func TestBuildObjectKeyNoPathTraversal(t *testing.T) {
	key := filepkg.BuildFileObjectKey(uuid.New(), uuid.New(), filepkg.FileCategoryMisc, "../evil.xlsx")
	require.NotContains(t, key, "..")
	require.False(t, strings.HasPrefix(key, "/"))
}

func TestSanitizeFilenameChineseAndFallback(t *testing.T) {
	require.Equal(t, "西咸 工勘表.xlsx", filepkg.SanitizeFilename("../西咸 工勘表.xlsx"))
	require.Equal(t, "uploaded_file", filepkg.SanitizeFilename("\x00"))
	require.Equal(t, "uploaded_file", filepkg.SanitizeFilename(".."))
}
