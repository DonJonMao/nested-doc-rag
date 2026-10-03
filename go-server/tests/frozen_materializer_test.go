package tests

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func sourceDigest(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }

func TestFrozenKnowledgeMaterializationRetainsSameNameSourcesAndVersions(t *testing.T) {
	ctx := context.Background()
	objects, err := storage.NewLocalStorage(t.TempDir())
	require.NoError(t, err)
	workspace, kb, version := uuid.New(), uuid.New(), uuid.New()
	snapshot := knowledge.BuildInputSnapshot{SchemaVersion: knowledge.BuildInputSchemaVersion, WorkspaceID: workspace, KnowledgeBaseID: kb, IndexVersionID: version, Collection: "shared", Namespace: "room"}
	contents := [][]byte{[]byte("机房A现网容量500kVA"), []byte("机房B规划容量900kVA")}
	for _, data := range contents {
		docID, fileID := uuid.New(), uuid.New()
		key := fileID.String() + "/source.txt"
		require.NoError(t, objects.Put(ctx, key, bytes.NewReader(data), int64(len(data)), "text/plain"))
		snapshot.Documents = append(snapshot.Documents, knowledge.BuildInputDocument{DocumentID: docID, FileID: fileID, Filename: "同名.txt", RelativePath: filepath.ToSlash(filepath.Join(docID.String(), fileID.String(), "同名.txt")), ObjectKey: key, SHA256: sourceDigest(data), SizeBytes: int64(len(data)), DocumentRole: knowledge.DocumentRoleKnowledgeBase})
	}
	materializer := knowledge.NewIngestionMaterializer(nil, nil, nil, objects, nil)
	encoded, err := knowledge.CanonicalSnapshotBytes(snapshot)
	require.NoError(t, err)
	out := t.TempDir()
	root, count, _, err := materializer.MaterializeBuildInput(ctx, workspace, encoded, out)
	require.NoError(t, err)
	require.Equal(t, 2, count)
	require.Equal(t, filepath.Join(out, "input", version.String()), root)
	for i, doc := range snapshot.Documents {
		actual, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(doc.RelativePath)))
		require.NoError(t, err)
		require.Equal(t, contents[i], actual)
	}
	snapshot.IndexVersionID = uuid.New()
	encoded, err = knowledge.CanonicalSnapshotBytes(snapshot)
	require.NoError(t, err)
	second, _, _, err := materializer.MaterializeBuildInput(ctx, workspace, encoded, out)
	require.NoError(t, err)
	require.NotEqual(t, root, second)
	// Changed source bytes fail before publishing a local replacement.
	doc := snapshot.Documents[0]
	require.NoError(t, objects.Put(ctx, doc.ObjectKey, bytes.NewReader([]byte("tampered")), 8, "text/plain"))
	_, _, _, err = materializer.MaterializeBuildInput(ctx, workspace, encoded, out)
	require.ErrorContains(t, err, "hash/size")
	actual, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(doc.RelativePath)))
	require.NoError(t, err)
	require.Equal(t, contents[0], actual)
	_, _, _, err = materializer.MaterializeBuildInput(ctx, uuid.New(), encoded, out)
	require.Error(t, err)
	snapshot.Documents[0].RelativePath = "../escape.txt"
	encoded, err = knowledge.CanonicalSnapshotBytes(snapshot)
	require.NoError(t, err)
	_, _, _, err = materializer.MaterializeBuildInput(ctx, workspace, encoded, out)
	require.Error(t, err)
}

func TestFrozenTemplateReadsOriginalObjectAndVerifiesHashAndSize(t *testing.T) {
	ctx := context.Background()
	objects, err := storage.NewLocalStorage(t.TempDir())
	require.NoError(t, err)
	data := []byte("frozen original workbook bytes")
	pin := formpkg.TemplatePin{WorkspaceID: uuid.New(), FileID: uuid.New(), ObjectKey: "original/template.xlsx", SHA256: sourceDigest(data), FileSize: int64(len(data)), Filename: "调研.xlsx"}
	require.NoError(t, objects.Put(ctx, pin.ObjectKey, bytes.NewReader(data), pin.FileSize, "application/octet-stream"))
	materializer := formpkg.NewTemplateMaterializer(nil, nil, objects, nil)
	encoded, err := json.Marshal(pin)
	require.NoError(t, err)
	out := t.TempDir()
	path, _, err := materializer.MaterializePinnedTemplate(ctx, pin.WorkspaceID, encoded, out)
	require.NoError(t, err)
	actual, err := os.ReadFile(path)
	require.NoError(t, err)
	require.Equal(t, data, actual)
	for _, mutate := range []func(*formpkg.TemplatePin){
		func(p *formpkg.TemplatePin) { p.FileSize++ },
		func(p *formpkg.TemplatePin) { p.SHA256 = sourceDigest([]byte("other")) },
		func(p *formpkg.TemplatePin) { p.WorkspaceID = uuid.New() },
		func(p *formpkg.TemplatePin) { p.Filename = "../escape.xlsx" },
	} {
		bad := pin
		mutate(&bad)
		encoded, err := json.Marshal(bad)
		require.NoError(t, err)
		_, _, err = materializer.MaterializePinnedTemplate(ctx, pin.WorkspaceID, encoded, out)
		require.Error(t, err)
	}
	actual, err = os.ReadFile(path)
	require.NoError(t, err)
	require.Equal(t, data, actual)
}
