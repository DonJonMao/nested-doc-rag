package tests

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	reviewpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/review"
	"github.com/google/uuid"
	"github.com/stretchr/testify/require"
)

func TestReviewParserParseReviewItemsJSONL(t *testing.T) {
	path := writeJSONL(t, `{"field_id":"f1","row_index":4,"target_cell":"B4","question_text":"功率","answer_status":"answered","answer_value":"10kW","confidence":0.91,"critic_flags":["answered_without_source"],"review_required":true,"writeback_allowed":false}`)
	parser := &reviewpkg.ArtifactReviewParser{}

	items, err := parser.ParseReviewItems(context.Background(), uuid.New(), uuid.New(), reviewpkg.ReviewArtifactPaths{ReviewItemsPath: path})

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, "f1", items[0].FieldID)
	require.Equal(t, "10kW", items[0].AnswerValue)
	require.Equal(t, reviewpkg.ReviewRiskHigh, items[0].RiskLevel)
	require.Equal(t, reviewpkg.ReviewStatusPending, items[0].Status)
}

func TestReviewParserParseRawPredictionsAndOverlays(t *testing.T) {
	raw := writeJSONL(t, `{"field_id":"f1","row_index":5,"target_cell":"C5","question_text":"楼层","answer_status":"answered","answer_value":"3F","source_chunk_ids":["c1"]}`)
	overlay := writeJSONL(t, `{"field_id":"f1","critic_flags":["needs_human_check"],"review_required":true,"writeback_allowed":true,"suggested_status":"answered","suggested_answer_value":"三层","reasons":["人工确认"]}`)
	parser := &reviewpkg.ArtifactReviewParser{}

	items, err := parser.ParseReviewItems(context.Background(), uuid.New(), uuid.New(), reviewpkg.ReviewArtifactPaths{PredictionsRawPath: raw, AgentOverlaysPath: overlay})

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, "3F", items[0].AnswerValue)
	require.Equal(t, "三层", items[0].SuggestedAnswerValue)
	require.True(t, items[0].WritebackAllowed)
	require.Equal(t, []string{"c1"}, items[0].SourceChunkIDs)
}

func TestReviewParserBadJSONLineSkipped(t *testing.T) {
	path := writeJSONL(t, `{"field_id":"f1","review_required":true}`+"\n"+`{bad json`)
	parser := &reviewpkg.ArtifactReviewParser{}

	items, err := parser.ParseReviewItems(context.Background(), uuid.New(), uuid.New(), reviewpkg.ReviewArtifactPaths{ReviewItemsPath: path})

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, 1, parser.LastParseErrors())
}

func TestReviewParserEmptyArtifactsReturnsEmpty(t *testing.T) {
	parser := &reviewpkg.ArtifactReviewParser{}

	items, err := parser.ParseReviewItems(context.Background(), uuid.New(), uuid.New(), reviewpkg.ReviewArtifactPaths{})

	require.NoError(t, err)
	require.Empty(t, items)
}

func TestReviewParserHighRiskFlags(t *testing.T) {
	overlay := writeJSONL(t, `{"field_id":"f1","critic_flags":["invalid_source_reference"],"review_required":true}`)
	parser := &reviewpkg.ArtifactReviewParser{}

	items, err := parser.ParseReviewItems(context.Background(), uuid.New(), uuid.New(), reviewpkg.ReviewArtifactPaths{AgentOverlaysPath: overlay})

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, reviewpkg.ReviewRiskHigh, items[0].RiskLevel)
}

func writeJSONL(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "items.jsonl")
	require.NoError(t, os.WriteFile(path, []byte(content), 0o644))
	return path
}
