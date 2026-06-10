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
	path := writeJSONL(t, `{"field_id":"f1","row_index":4,"target_cell":"B4","question_text":"功率","answer_status":"answered","answer_value":"10kW","confidence":0.91,"critic_flags":["needs_human_check"],"risk_level":"high","review_required":true,"writeback_allowed":false,"source_chunk_ids":["c1"],"reference_source_documents":[{"filename":"能力清单.xlsx"}]}`)
	parser := &reviewpkg.ArtifactReviewParser{}
	runID := uuid.New()
	workspaceID := uuid.New()

	items, err := parser.ParseReviewItems(context.Background(), runID, workspaceID, reviewpkg.ReviewArtifactPaths{ReviewItemsPath: path})

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, workspaceID, items[0].WorkspaceID)
	require.Equal(t, runID, items[0].RunID)
	require.Equal(t, "f1", items[0].FieldID)
	require.Equal(t, 4, items[0].RowIndex)
	require.Equal(t, "B4", items[0].TargetCell)
	require.Equal(t, "10kW", items[0].AnswerValue)
	require.NotEmpty(t, items[0].RawPayload)
	require.True(t, items[0].ReviewRequired)
	require.False(t, items[0].WritebackAllowed)
	require.Equal(t, reviewpkg.ReviewRiskHigh, items[0].RiskLevel)
	require.Equal(t, reviewpkg.ReviewStatusPending, items[0].Status)
	require.Equal(t, []string{"c1"}, items[0].SourceChunkIDs)
	require.Equal(t, "能力清单.xlsx", items[0].ReferenceSourceDocuments[0]["filename"])
}

func TestReviewParserParseRawPredictionsAndOverlays(t *testing.T) {
	raw := writeJSONL(t, `{"field_id":"f1","row_index":5,"target_cell":"C5","question_text":"楼层","answer_status":"answered","answer_value":"3F","source_chunk_ids":["c1"]}`)
	overlay := writeJSONL(t, `{"field_id":"f1","critic_flags":["needs_human_check"],"review_required":true,"writeback_allowed":true,"suggested_status":"answered","suggested_answer_value":"三层","reasons":["人工确认"]}`)
	parser := &reviewpkg.ArtifactReviewParser{}

	items, err := parser.ParseReviewItems(context.Background(), uuid.New(), uuid.New(), reviewpkg.ReviewArtifactPaths{PredictionsRawPath: raw, AgentOverlaysPath: overlay})

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, "3F", items[0].AnswerValue)
	require.Equal(t, "answered", items[0].AnswerStatus)
	require.Equal(t, "三层", items[0].SuggestedAnswerValue)
	require.Equal(t, "answered", items[0].SuggestedStatus)
	require.True(t, items[0].WritebackAllowed)
	require.Equal(t, []string{"c1"}, items[0].SourceChunkIDs)
	require.Equal(t, []string{"needs_human_check"}, items[0].CriticFlags)
	require.Equal(t, []string{"人工确认"}, items[0].Reasons)
	require.NotEmpty(t, items[0].OverlayPayload)
}

func TestReviewParserMissingReviewItemsButOverlayWorks(t *testing.T) {
	raw := writeJSONL(t, `{"field_id":"f2","answer_status":"partial_clue","answer_value":"候选答案"}`)
	overlay := writeJSONL(t, `{"field_id":"f2","review_required":true,"critic_flags":["liquid_cooling_scope_mismatch"]}`)
	parser := &reviewpkg.ArtifactReviewParser{}

	items, err := parser.ParseReviewItems(context.Background(), uuid.New(), uuid.New(), reviewpkg.ReviewArtifactPaths{PredictionsRawPath: raw, AgentOverlaysPath: overlay})

	require.NoError(t, err)
	require.Len(t, items, 1)
	require.Equal(t, "候选答案", items[0].AnswerValue)
	require.Equal(t, reviewpkg.ReviewRiskHigh, items[0].RiskLevel)
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
	for _, flag := range []string{"invalid_source_reference", "answered_without_source", "liquid_cooling_scope_mismatch"} {
		t.Run(flag, func(t *testing.T) {
			overlay := writeJSONL(t, `{"field_id":"f1","critic_flags":["`+flag+`"],"review_required":true}`)
			parser := &reviewpkg.ArtifactReviewParser{}

			items, err := parser.ParseReviewItems(context.Background(), uuid.New(), uuid.New(), reviewpkg.ReviewArtifactPaths{AgentOverlaysPath: overlay})

			require.NoError(t, err)
			require.Len(t, items, 1)
			require.Equal(t, reviewpkg.ReviewRiskHigh, items[0].RiskLevel)
		})
	}
}

func writeJSONL(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "items.jsonl")
	require.NoError(t, os.WriteFile(path, []byte(content), 0o644))
	return path
}
