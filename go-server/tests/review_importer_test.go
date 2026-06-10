package tests

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/artifact"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	reviewpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/review"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/runevent"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

func TestReviewImporterImportsFromManifestArtifacts(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{
		artifact.TypeReviewItems:          "review_items.jsonl",
		artifact.TypePredictionsRaw:       "predictions_raw.jsonl",
		artifact.TypeAgentOverlays:        "agent_overlays.jsonl",
		artifact.TypePredictionsAgentView: "predictions_agent_view.jsonl",
	})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "review_items.jsonl"), []byte(`{"field_id":"f1","review_required":true,"writeback_allowed":true}`), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions_raw.jsonl"), []byte(`{"field_id":"f1","answer_value":"raw"}`), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "agent_overlays.jsonl"), []byte(`{"field_id":"f1","review_required":true,"writeback_allowed":true}`), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions_agent_view.jsonl"), []byte(`{"field_id":"f1"}`), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	repo := newFakeReviewRepo()
	importer := reviewpkg.NewImporter(repo, nil, nil, zap.NewNop())

	result, err := importer.ImportForFillRun(context.Background(), uuid.New(), uuid.New(), manifest)

	require.NoError(t, err)
	require.Equal(t, 1, result.TotalParsed)
	require.Equal(t, 1, result.Created)
	require.Equal(t, 1, result.ReviewRequired)
	require.Equal(t, 1, result.WritebackAllowed)
	require.Equal(t, 1, repo.upserts)
}

func TestReviewImporterMissingReviewArtifactsNonFatal(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{artifact.TypePredictions: "predictions.json"})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "predictions.json"), []byte("{}"), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	importer := reviewpkg.NewImporter(newFakeReviewRepo(), nil, nil, zap.NewNop())

	result, err := importer.ImportForFillRun(context.Background(), uuid.New(), uuid.New(), manifest)

	require.NoError(t, err)
	require.Zero(t, result.TotalParsed)
}

func TestReviewImporterReportsParseErrors(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{artifact.TypeReviewItems: "review_items.jsonl"})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "review_items.jsonl"), []byte(`{"field_id":"f1"}`+"\n"+`{bad`), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	importer := reviewpkg.NewImporter(newFakeReviewRepo(), nil, nil, zap.NewNop())

	result, err := importer.ImportForFillRun(context.Background(), uuid.New(), uuid.New(), manifest)

	require.NoError(t, err)
	require.Equal(t, 1, result.ParseErrors)
	require.Equal(t, 1, result.TotalParsed)
}

func TestReviewImporterParserErrorReturnsError(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{artifact.TypeReviewItems: "review_items.jsonl"})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "review_items.jsonl"), []byte(`{bad`+"\n"+`{also bad`), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	importer := reviewpkg.NewImporter(newFakeReviewRepo(), nil, nil, zap.NewNop())

	result, err := importer.ImportForFillRun(context.Background(), uuid.New(), uuid.New(), manifest)

	require.Error(t, err)
	require.Equal(t, 2, result.ParseErrors)
}

func TestReviewImporterEmitsRunEvent(t *testing.T) {
	runDir := writeTestManifest(t, map[string]string{artifact.TypeReviewItems: "review_items.jsonl"})
	require.NoError(t, os.WriteFile(filepath.Join(runDir, "review_items.jsonl"), []byte(`{"field_id":"f1","review_required":true}`), 0o644))
	manifest, err := pythonpkg.LoadRunManifestFromDir(runDir)
	require.NoError(t, err)
	eventRepo := &fakeRunEventRepo{}
	importer := reviewpkg.NewImporter(newFakeReviewRepo(), nil, eventRepo, zap.NewNop())

	result, err := importer.ImportForFillRun(context.Background(), uuid.New(), uuid.New(), manifest)

	require.NoError(t, err)
	require.Equal(t, 1, result.TotalParsed)
	require.Len(t, eventRepo.events, 1)
	require.Equal(t, runevent.EventReviewItemsImported, eventRepo.events[0].EventType)
	require.Equal(t, 1, eventRepo.events[0].Payload["total_parsed"])
	require.Equal(t, 1, eventRepo.events[0].Payload["created"])
	require.Equal(t, 0, eventRepo.events[0].Payload["updated"])
	require.Equal(t, 0, eventRepo.events[0].Payload["parse_errors"])
	require.Equal(t, 1, eventRepo.events[0].Payload["review_required"])
	require.Equal(t, 0, eventRepo.events[0].Payload["writeback_allowed"])
}
