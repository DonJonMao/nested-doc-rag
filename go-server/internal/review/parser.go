package review

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/google/uuid"
)

type ReviewArtifactPaths struct {
	ReviewItemsPath          string
	PredictionsRawPath       string
	AgentOverlaysPath        string
	PredictionsAgentViewPath string
}

type ArtifactReviewParser struct {
	lastParseErrors int
}

func (p *ArtifactReviewParser) LastParseErrors() int {
	if p == nil {
		return 0
	}
	return p.lastParseErrors
}

func (p *ArtifactReviewParser) ParseReviewItems(ctx context.Context, runID uuid.UUID, workspaceID uuid.UUID, paths ReviewArtifactPaths) ([]ReviewItem, error) {
	if p == nil {
		p = &ArtifactReviewParser{}
	}
	p.lastParseErrors = 0
	itemsByKey := make(map[string]ReviewItem)
	if fileExists(paths.ReviewItemsPath) {
		rows, parseErrors, err := readJSONL(ctx, paths.ReviewItemsPath)
		p.lastParseErrors += parseErrors
		if err != nil {
			return nil, err
		}
		for _, row := range rows {
			item := reviewItemFromMap(row, nil, runID, workspaceID)
			itemsByKey[itemKey(item)] = item
		}
	}

	rawByKey := make(map[string]map[string]any)
	if fileExists(paths.PredictionsRawPath) {
		rows, parseErrors, err := readJSONL(ctx, paths.PredictionsRawPath)
		p.lastParseErrors += parseErrors
		if err != nil {
			return nil, err
		}
		for _, row := range rows {
			rawByKey[recordKey(row)] = row
		}
	}

	if len(itemsByKey) == 0 {
		for key, raw := range rawByKey {
			item := reviewItemFromMap(raw, nil, runID, workspaceID)
			itemsByKey[key] = item
		}
	}

	if fileExists(paths.AgentOverlaysPath) {
		rows, parseErrors, err := readJSONL(ctx, paths.AgentOverlaysPath)
		p.lastParseErrors += parseErrors
		if err != nil {
			return nil, err
		}
		for _, overlay := range rows {
			key := recordKey(overlay)
			raw := rawByKey[key]
			item, ok := itemsByKey[key]
			if ok {
				item = mergeOverlay(item, overlay)
				if raw != nil && len(item.RawPayload) == 0 {
					item.RawPayload = copyMap(raw)
				}
			} else {
				item = reviewItemFromMap(raw, overlay, runID, workspaceID)
			}
			itemsByKey[itemKey(item)] = item
		}
	}

	if len(itemsByKey) == 0 {
		if p.lastParseErrors > 0 {
			return nil, fmt.Errorf("all review artifact lines failed to parse")
		}
		return []ReviewItem{}, nil
	}
	items := make([]ReviewItem, 0, len(itemsByKey))
	for _, item := range itemsByKey {
		normalizeReviewItem(&item)
		items = append(items, item)
	}
	return items, nil
}

func readJSONL(ctx context.Context, path string) ([]map[string]any, int, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer file.Close()
	var rows []map[string]any
	parseErrors := 0
	reader := bufio.NewReader(file)
	for {
		if err := ctx.Err(); err != nil {
			return nil, parseErrors, err
		}
		line, err := reader.ReadString('\n')
		if err != nil && !errors.Is(err, io.EOF) {
			return nil, parseErrors, err
		}
		line = strings.TrimSpace(line)
		if line != "" {
			var payload map[string]any
			if jsonErr := json.Unmarshal([]byte(line), &payload); jsonErr != nil {
				parseErrors++
			} else {
				rows = append(rows, payload)
			}
		}
		if errors.Is(err, io.EOF) {
			break
		}
	}
	if len(rows) == 0 && parseErrors > 0 {
		return nil, parseErrors, fmt.Errorf("all JSONL lines failed in %s", path)
	}
	return rows, parseErrors, nil
}

func reviewItemFromMap(raw map[string]any, overlay map[string]any, runID uuid.UUID, workspaceID uuid.UUID) ReviewItem {
	source := raw
	if source == nil {
		source = overlay
	}
	item := ReviewItem{
		ID:                                uuid.New(),
		WorkspaceID:                       workspaceID,
		RunID:                             runID,
		FieldID:                           getString(source, "field_id", "field", "id"),
		RowIndex:                          getInt(source, "row_index", "row"),
		TargetCell:                        getString(source, "target_cell", "cell"),
		QuestionText:                      getString(source, "question_text", "question"),
		AnswerStatus:                      getString(source, "answer_status", "status_raw"),
		AnswerValue:                       getString(source, "answer_value", "answer"),
		Confidence:                        getFloat(source, "confidence"),
		SourceChunkIDs:                    getStringSlice(source, "source_chunk_ids"),
		EvidenceAttachmentIDs:             getStringSlice(source, "evidence_attachment_ids"),
		ReferenceChunkIDs:                 getStringSlice(source, "reference_chunk_ids"),
		ReferenceSourceDocuments:          getMapSlice(source, "reference_source_documents"),
		ReferenceSnippets:                 getStringSlice(source, "reference_snippets"),
		CriticFlags:                       getStringSlice(source, "critic_flags", "flags"),
		ReviewRequired:                    getBoolDefault(source, true, "review_required"),
		WritebackAllowed:                  getBoolDefault(source, false, "writeback_allowed"),
		SuggestedStatus:                   getString(source, "suggested_status"),
		SuggestedAnswerValue:              getString(source, "suggested_answer_value", "suggested_answer"),
		SuggestedReferenceSourceDocuments: getMapSlice(source, "suggested_reference_source_documents"),
		Reasons:                           getStringSlice(source, "reasons"),
		WritebackStatus:                   getString(source, "writeback_status", "field_status", "status"),
		WritebackAction:                   getString(source, "writeback_action"),
		EvidenceRefs:                      getMapSlice(source, "evidence_refs"),
		WritebackErrorCode:                getString(source, "writeback_error_code", "error_code"),
		RiskLevel:                         getString(source, "risk_level"),
		Status:                            ReviewStatusPending,
		RawPayload:                        copyMap(raw),
		OverlayPayload:                    copyMap(overlay),
	}
	if overlay != nil {
		item = mergeOverlay(item, overlay)
	}
	if item.RiskLevel == "" {
		item.RiskLevel = riskFromFlags(item.CriticFlags, item.ReviewRequired)
	}
	if !item.ReviewRequired {
		item.Status = ReviewStatusIgnored
	}
	return item
}

func mergeOverlay(item ReviewItem, overlay map[string]any) ReviewItem {
	item.OverlayPayload = copyMap(overlay)
	item.CriticFlags = getStringSlice(overlay, "critic_flags", "flags")
	item.ReviewRequired = getBoolDefault(overlay, item.ReviewRequired, "review_required")
	item.WritebackAllowed = getBoolDefault(overlay, item.WritebackAllowed, "writeback_allowed")
	item.SuggestedStatus = getString(overlay, "suggested_status")
	item.SuggestedAnswerValue = getString(overlay, "suggested_answer_value", "suggested_answer")
	item.SuggestedReferenceSourceDocuments = getMapSlice(overlay, "suggested_reference_source_documents")
	item.Reasons = getStringSlice(overlay, "reasons")
	if item.WritebackStatus == "" {
		item.WritebackStatus = getString(overlay, "writeback_status", "field_status", "status")
	}
	if item.WritebackAction == "" {
		item.WritebackAction = getString(overlay, "writeback_action")
	}
	if len(item.EvidenceRefs) == 0 {
		item.EvidenceRefs = getMapSlice(overlay, "evidence_refs")
	}
	if item.WritebackErrorCode == "" {
		item.WritebackErrorCode = getString(overlay, "writeback_error_code", "error_code")
	}
	item.RiskLevel = getString(overlay, "risk_level")
	if item.RiskLevel == "" {
		item.RiskLevel = riskFromFlags(item.CriticFlags, item.ReviewRequired)
	}
	if !item.ReviewRequired {
		item.Status = ReviewStatusIgnored
	} else if item.Status == "" || item.Status == ReviewStatusIgnored {
		item.Status = ReviewStatusPending
	}
	return item
}

func riskFromFlags(flags []string, reviewRequired bool) string {
	highRisk := map[string]struct{}{
		"invalid_source_reference":        {},
		"answered_without_source":         {},
		"liquid_cooling_scope_mismatch":   {},
		"answered_from_global_intro_risk": {},
	}
	for _, flag := range flags {
		if _, ok := highRisk[strings.TrimSpace(flag)]; ok {
			return ReviewRiskHigh
		}
	}
	if reviewRequired {
		return ReviewRiskMedium
	}
	return ReviewRiskLow
}

func recordKey(payload map[string]any) string {
	if payload == nil {
		return ""
	}
	fieldID := getString(payload, "field_id", "field", "id")
	if fieldID != "" {
		return "field:" + fieldID
	}
	return fmt.Sprintf("cell:%d:%s", getInt(payload, "row_index", "row"), getString(payload, "target_cell", "cell"))
}

func itemKey(item ReviewItem) string {
	if strings.TrimSpace(item.FieldID) != "" {
		return "field:" + item.FieldID
	}
	return fmt.Sprintf("cell:%d:%s", item.RowIndex, item.TargetCell)
}

func fileExists(path string) bool {
	if strings.TrimSpace(path) == "" {
		return false
	}
	info, err := os.Stat(path)
	return err == nil && !info.IsDir() && info.Size() > 0
}

func getString(payload map[string]any, keys ...string) string {
	if payload == nil {
		return ""
	}
	for _, key := range keys {
		value, ok := payload[key]
		if !ok || value == nil {
			continue
		}
		switch typed := value.(type) {
		case string:
			return strings.TrimSpace(typed)
		default:
			return strings.TrimSpace(fmt.Sprint(typed))
		}
	}
	return ""
}

func getInt(payload map[string]any, keys ...string) int {
	for _, key := range keys {
		value, ok := payload[key]
		if !ok || value == nil {
			continue
		}
		switch typed := value.(type) {
		case int:
			return typed
		case float64:
			return int(typed)
		case json.Number:
			n, _ := typed.Int64()
			return int(n)
		case string:
			var parsed int
			if _, err := fmt.Sscanf(typed, "%d", &parsed); err == nil {
				return parsed
			}
		}
	}
	return 0
}

func getFloat(payload map[string]any, keys ...string) float64 {
	for _, key := range keys {
		value, ok := payload[key]
		if !ok || value == nil {
			continue
		}
		switch typed := value.(type) {
		case float64:
			return typed
		case float32:
			return float64(typed)
		case int:
			return float64(typed)
		case json.Number:
			n, _ := typed.Float64()
			return n
		}
	}
	return 0
}

func getBoolDefault(payload map[string]any, fallback bool, keys ...string) bool {
	for _, key := range keys {
		value, ok := payload[key]
		if !ok || value == nil {
			continue
		}
		switch typed := value.(type) {
		case bool:
			return typed
		case string:
			return typed == "true" || typed == "1"
		}
	}
	return fallback
}

func getStringSlice(payload map[string]any, keys ...string) []string {
	for _, key := range keys {
		value, ok := payload[key]
		if !ok || value == nil {
			continue
		}
		switch typed := value.(type) {
		case []string:
			return append([]string(nil), typed...)
		case []any:
			out := make([]string, 0, len(typed))
			for _, item := range typed {
				if item != nil {
					out = append(out, fmt.Sprint(item))
				}
			}
			return out
		case string:
			if strings.TrimSpace(typed) == "" {
				return []string{}
			}
			return []string{strings.TrimSpace(typed)}
		}
	}
	return []string{}
}

func getMapSlice(payload map[string]any, keys ...string) []map[string]any {
	for _, key := range keys {
		value, ok := payload[key]
		if !ok || value == nil {
			continue
		}
		switch typed := value.(type) {
		case []map[string]any:
			return append([]map[string]any(nil), typed...)
		case []any:
			out := make([]map[string]any, 0, len(typed))
			for _, item := range typed {
				if m, ok := item.(map[string]any); ok {
					out = append(out, copyMap(m))
				}
			}
			return out
		case map[string]any:
			return []map[string]any{copyMap(typed)}
		}
	}
	return []map[string]any{}
}

func copyMap(input map[string]any) map[string]any {
	if input == nil {
		return map[string]any{}
	}
	out := make(map[string]any, len(input))
	for key, value := range input {
		out[key] = value
	}
	return out
}
