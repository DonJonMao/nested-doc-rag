package tests

import (
	"strings"
	"testing"

	reviewpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/review"
	"github.com/stretchr/testify/require"
)

func TestReviewExporterJSON(t *testing.T) {
	data, err := reviewpkg.ExportJSON([]reviewpkg.ReviewItem{{FieldID: "f1", AnswerValue: "答案"}})

	require.NoError(t, err)
	require.Contains(t, string(data), `"field_id": "f1"`)
	require.Contains(t, string(data), "答案")
}

func TestReviewExporterCSVEscapingAndChinese(t *testing.T) {
	data, err := reviewpkg.ExportCSV([]reviewpkg.ReviewItem{{
		RowIndex:         4,
		TargetCell:       "B4",
		QuestionText:     "机房,名称",
		AnswerValue:      "西咸\"4号楼\"\n301",
		CriticFlags:      []string{"flag1", "flag2"},
		RiskLevel:        reviewpkg.ReviewRiskHigh,
		Status:           reviewpkg.ReviewStatusEdited,
		WritebackAllowed: true,
		EditedAnswer:     "人工确认",
		ReviewComment:    "现场确认",
	}})

	require.NoError(t, err)
	text := string(data)
	require.Contains(t, text, "机房,名称")
	require.Contains(t, text, "\"西咸\"\"4号楼\"")
	require.Contains(t, text, "人工确认")
	require.True(t, strings.HasPrefix(text, "row_index,target_cell"))
}
