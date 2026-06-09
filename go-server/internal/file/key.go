package file

import (
	"path"
	"strings"

	"github.com/google/uuid"
)

func BuildFileObjectKey(workspaceID uuid.UUID, fileID uuid.UUID, category string, filename string) string {
	filename = SanitizeFilename(filename)
	dir := "files"
	switch category {
	case FileCategoryKnowledgeDocument:
		dir = "documents"
	case FileCategoryFormTemplate:
		dir = "forms"
	case FileCategoryProofAttachment:
		dir = "attachments"
	}
	return cleanKey(path.Join("workspaces", workspaceID.String(), dir, fileID.String(), filename))
}

func BuildArtifactObjectKey(workspaceID uuid.UUID, runID uuid.UUID, artifactID uuid.UUID, filename string) string {
	filename = SanitizeFilename(filename)
	return cleanKey(path.Join("workspaces", workspaceID.String(), "runs", runID.String(), "artifacts", artifactID.String(), filename))
}

func cleanKey(key string) string {
	key = strings.ReplaceAll(key, "\\", "/")
	key = strings.TrimPrefix(path.Clean(key), "/")
	if key == "." || strings.HasPrefix(key, "../") || strings.Contains(key, "/../") {
		return ""
	}
	return key
}
