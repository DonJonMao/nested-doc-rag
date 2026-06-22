package review

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"strconv"
	"strings"
)

func ExportJSON(items []ReviewItem) ([]byte, error) {
	if items == nil {
		items = []ReviewItem{}
	}
	return json.MarshalIndent(items, "", "  ")
}

func ExportCSV(items []ReviewItem) ([]byte, error) {
	var buf bytes.Buffer
	writer := csv.NewWriter(&buf)
	if err := writer.Write([]string{
		"row_index",
		"target_cell",
		"question_text",
		"answer_status",
		"answer_value",
		"confidence",
		"critic_flags",
		"risk_level",
		"status",
		"writeback_status",
		"writeback_action",
		"writeback_allowed",
		"writeback_error_code",
		"suggested_status",
		"edited_answer",
		"review_comment",
	}); err != nil {
		return nil, err
	}
	for _, item := range items {
		if err := writer.Write([]string{
			strconv.Itoa(item.RowIndex),
			item.TargetCell,
			item.QuestionText,
			item.AnswerStatus,
			item.AnswerValue,
			strconv.FormatFloat(item.Confidence, 'f', -1, 64),
			strings.Join(item.CriticFlags, "|"),
			item.RiskLevel,
			item.Status,
			item.WritebackStatus,
			item.WritebackAction,
			strconv.FormatBool(item.WritebackAllowed),
			item.WritebackErrorCode,
			item.SuggestedStatus,
			item.EditedAnswer,
			item.ReviewComment,
		}); err != nil {
			return nil, err
		}
	}
	writer.Flush()
	if err := writer.Error(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}
