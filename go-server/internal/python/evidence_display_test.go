package python

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestEvidenceDisplayJSONPreservesAcquisitionAddressAndZero(t *testing.T) {
	// Recorded zero dimensions are retained as optional metadata. This does not
	// grant them native address validity or turn unavailable provenance into exact.
	raw := []byte(`{
		"summary":{"exact":0,"ambiguous":0,"unmatched":0,"unavailable":4},
		"fields":[{
			"field_id":"native-field","answer_value":"600kW","answer_status":"answered",
			"writeback_status":"confirmed","writeback_action":"skipped_non_empty_cell","writeback_allowed":false,
			"old_value":0,"new_value":0,"existing_value_policy":"preserve",
			"acquisition":{"strategy":"sufficiency_guided","acquisition_rounds":2,"qdrant_query_calls":null,
				"rounds":[{"retrieval_round":1,"missing_facts":["柴油储量"],"hit_count":2,"evidence_gain":0}],
				"final_sufficiency":{"sufficient":false,"reason":"仍不足","missing_facts":["柴油储量"]}},
			"evidence_refs":[
				{"file_name":"参数.xlsx","relative_path":"doc/file/参数.xlsx","source_anchor":"南501!row 2",
				 "sheet_name":"南501","cell":"B2","cell_range":"A2:B2","row_index":2,"column_index":1,
				 "qdrant_point_id":"actual-point","provenance":{"match_status":"unavailable","reason":"missing_quote"}},
				{"file_name":"设备.docx","source_anchor":"table 2 row 4","table_index":2,"row_index":4,
				 "provenance":{"match_status":"unavailable","reason":"missing_quote"}},
				{"file_name":"巡检.docx","source_anchor":"paragraph 14","paragraph_index":14,
				 "provenance":{"match_status":"unavailable","reason":"missing_quote"}},
				{"file_name":"历史元数据.docx","row_index":0,"column_index":0,"table_index":0,"paragraph_index":0,"page":0,
				 "provenance":{"match_status":"unavailable","reason":"historical_preview"}}
			]
		}]
	}`)
	var evidence ManifestEvidence
	require.NoError(t, json.Unmarshal(raw, &evidence))
	require.NoError(t, evidence.Validate())
	before, err := json.Marshal(evidence)
	require.NoError(t, err)
	display := EvidenceForDisplay(&evidence)
	encoded, err := json.Marshal(display)
	require.NoError(t, err)
	after, err := json.Marshal(evidence)
	require.NoError(t, err)
	require.JSONEq(t, string(before), string(after), "display normalization must not mutate the caller's slices")
	var actual, original map[string]any
	require.NoError(t, json.Unmarshal(encoded, &actual))
	require.NoError(t, json.Unmarshal(raw, &original))
	field := actual["fields"].([]any)[0].(map[string]any)
	input := original["fields"].([]any)[0].(map[string]any)
	for _, key := range []string{"answer_value", "answer_status", "writeback_status", "writeback_action", "writeback_allowed", "acquisition", "old_value", "new_value", "existing_value_policy"} {
		require.Contains(t, field, key)
		require.Equal(t, input[key], field[key], key)
	}
	require.Equal(t, []any{}, field["reasons"])
	refs := field["evidence_refs"].([]any)
	inputRefs := input["evidence_refs"].([]any)
	for i := range refs {
		ref := refs[i].(map[string]any)
		for key, value := range inputRefs[i].(map[string]any) {
			require.Contains(t, ref, key)
			if key == "provenance" {
				for provenanceKey, provenanceValue := range value.(map[string]any) {
					require.Equal(t, provenanceValue, ref[key].(map[string]any)[provenanceKey], "ref %d provenance %s", i, provenanceKey)
				}
			} else {
				require.Equal(t, value, ref[key], "ref %d %s", i, key)
			}
		}
		require.Equal(t, []any{}, ref["bbox"])
		require.Equal(t, []any{}, ref["proof_attachment_ids"])
		require.Equal(t, []any{}, ref["proof_attachments"])
	}
	for _, key := range []string{"row_index", "column_index", "table_index", "paragraph_index"} {
		require.Contains(t, refs[3], key)
		require.Equal(t, float64(0), refs[3].(map[string]any)[key])
	}
	require.NotContains(t, refs[0], "table_index")
	require.NotContains(t, refs[1], "paragraph_index")
	require.NotContains(t, refs[2], "row_index")
	var roundTrip ManifestEvidence
	require.NoError(t, json.Unmarshal(encoded, &roundTrip))
	require.NoError(t, roundTrip.Validate())
	require.Equal(t, evidence.Fields[0].Acquisition, roundTrip.Fields[0].Acquisition)
}

func TestEvidenceDisplayLegacyJSONDoesNotInventExtensions(t *testing.T) {
	var legacy ManifestEvidence
	require.NoError(t, json.Unmarshal([]byte(`{
		"summary":{"exact":0,"ambiguous":0,"unmatched":0,"unavailable":1},
		"fields":[{"field_id":"old","answer_value":"旧答案","evidence_refs":[
			{"text_preview":"旧摘录","provenance":{"match_status":"unavailable","reason":"historical_preview"}}
		]}]
	}`), &legacy))
	require.NoError(t, legacy.Validate())
	encoded, err := json.Marshal(EvidenceForDisplay(&legacy))
	require.NoError(t, err)
	var value map[string]any
	require.NoError(t, json.Unmarshal(encoded, &value))
	field := value["fields"].([]any)[0].(map[string]any)
	for _, key := range []string{"acquisition", "old_value", "new_value", "existing_value_policy"} {
		require.NotContains(t, field, key)
	}
	ref := field["evidence_refs"].([]any)[0].(map[string]any)
	for _, key := range []string{"cell_range", "row_index", "column_index", "table_index", "paragraph_index", "evidence_kind"} {
		require.NotContains(t, ref, key)
	}
	require.Equal(t, "旧答案", field["answer_value"])
	require.Equal(t, "旧摘录", ref["text_preview"])
	require.Equal(t, "unavailable", ref["provenance"].(map[string]any)["match_status"])
}

func TestEvidenceDisplayNilKeepsStableEmptyArrays(t *testing.T) {
	encoded, err := json.Marshal(EvidenceForDisplay(nil))
	require.NoError(t, err)
	require.JSONEq(t, `{"summary":{"exact":0,"ambiguous":0,"unmatched":0,"unavailable":0},"fields":[]}`, string(encoded))
}
