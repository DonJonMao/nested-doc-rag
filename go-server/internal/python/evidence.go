package python

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
	"unicode"
)

// ManifestEvidence describes where a quotation was located, independently of
// whether that quotation supports an answer or permits writing to the workbook.
type ManifestEvidence struct {
	Summary ManifestEvidenceSummary `json:"summary"`
	Fields  []ManifestEvidenceField `json:"fields"`
}

type ManifestEvidenceSummary struct {
	Exact       int `json:"exact"`
	Ambiguous   int `json:"ambiguous"`
	Unmatched   int `json:"unmatched"`
	Unavailable int `json:"unavailable"`
}

type ManifestEvidenceField struct {
	FieldID             string                `json:"field_id"`
	FieldKey            string                `json:"field_key"`
	QuestionText        string                `json:"question_text"`
	AnswerValue         any                   `json:"answer_value"`
	AnswerStatus        string                `json:"answer_status"`
	TargetCell          string                `json:"target_cell"`
	SheetName           string                `json:"sheet_name"`
	RowIndex            int                   `json:"row_index"`
	WritebackStatus     string                `json:"writeback_status"`
	WritebackAction     string                `json:"writeback_action"`
	WritebackAllowed    bool                  `json:"writeback_allowed"`
	Reasons             []string              `json:"reasons"`
	EvidenceRefs        []ManifestEvidenceRef `json:"evidence_refs"`
	Acquisition         map[string]any        `json:"acquisition,omitempty"`
	OldValue            any                   `json:"old_value,omitempty"`
	NewValue            any                   `json:"new_value,omitempty"`
	ExistingValuePolicy string                `json:"existing_value_policy,omitempty"`
}

type ManifestEvidenceRef struct {
	ChunkID            string                       `json:"chunk_id"`
	Namespace          string                       `json:"namespace"`
	SourceType         string                       `json:"source_type"`
	EvidenceKind       string                       `json:"evidence_kind,omitempty"`
	CorpusLayer        string                       `json:"corpus_layer"`
	RetrievalLayer     string                       `json:"retrieval_layer"`
	SourceAnchor       string                       `json:"source_anchor"`
	Anchor             string                       `json:"anchor"`
	FileName           string                       `json:"file_name"`
	DocumentName       string                       `json:"document_name"`
	RelativePath       string                       `json:"relative_path"`
	DocumentID         string                       `json:"document_id"`
	SourceDocumentHash string                       `json:"source_document_hash"`
	ObjectKey          string                       `json:"object_key"`
	ObjectVersionID    string                       `json:"object_version_id"`
	QdrantPointID      string                       `json:"qdrant_point_id"`
	Page               any                          `json:"page"`
	SheetName          string                       `json:"sheet_name"`
	Cell               string                       `json:"cell"`
	CellRange          string                       `json:"cell_range,omitempty"`
	RowIndex           *int                         `json:"row_index,omitempty"`
	ColumnIndex        *int                         `json:"column_index,omitempty"`
	TableIndex         *int                         `json:"table_index,omitempty"`
	ParagraphIndex     *int                         `json:"paragraph_index,omitempty"`
	BBox               []float64                    `json:"bbox"`
	Caption            string                       `json:"caption"`
	ImageObjectKey     string                       `json:"image_object_key"`
	ProofAttachmentIDs []string                     `json:"proof_attachment_ids"`
	ProofAttachments   []ManifestEvidenceAttachment `json:"proof_attachments"`
	TextPreview        string                       `json:"text_preview"`
	Provenance         *EvidenceProvenance          `json:"provenance"`
}

type ManifestEvidenceAttachment struct {
	AttachmentID     string `json:"attachment_id"`
	SourceCell       string `json:"source_cell"`
	MediaPath        string `json:"media_path"`
	MediaContentType string `json:"media_content_type"`
	AttachmentType   string `json:"attachment_type"`
	ImageID          string `json:"image_id"`
	RelationshipID   string `json:"relationship_id"`
	ImagePath        string `json:"image_path"`
	Caption          string `json:"caption"`
}

type EvidenceProvenance struct {
	MatchStatus    string `json:"match_status"`
	Reason         string `json:"reason"`
	Quote          string `json:"quote"`
	Start          *int   `json:"start"`
	End            *int   `json:"end"`
	SourceText     string `json:"source_text"`
	SourceTextHash string `json:"source_text_hash"`
	TextSpace      string `json:"text_space"`
	IndexVersion   string `json:"index_version"`
}

func (e *ManifestEvidence) Validate() error {
	if e == nil {
		return nil // Manifests produced before evidence provenance remain readable.
	}
	var counted ManifestEvidenceSummary
	for _, field := range e.Fields {
		if strings.TrimSpace(field.FieldID) == "" && strings.TrimSpace(field.FieldKey) == "" {
			return fmt.Errorf("evidence field identity is missing")
		}
		if field.RowIndex < 0 {
			return fmt.Errorf("evidence field row_index is invalid")
		}
		for _, ref := range field.EvidenceRefs {
			if ref.ImageObjectKey != "" && !SafeEvidenceObjectKey(ref.ImageObjectKey) {
				return fmt.Errorf("evidence image object key is unsafe")
			}
			if err := ref.Provenance.validate(); err != nil {
				return err
			}
			switch ref.Provenance.MatchStatus {
			case "exact":
				counted.Exact++
			case "ambiguous":
				counted.Ambiguous++
			case "unmatched":
				counted.Unmatched++
			case "unavailable":
				counted.Unavailable++
			}
		}
	}
	if counted != e.Summary {
		return fmt.Errorf("evidence summary does not match reference counts")
	}
	return nil
}

func (p *EvidenceProvenance) validate() error {
	if p == nil {
		return fmt.Errorf("evidence provenance is missing")
	}
	switch p.MatchStatus {
	case "exact", "ambiguous", "unmatched", "unavailable":
	default:
		return fmt.Errorf("evidence provenance match_status is invalid")
	}
	if p.SourceText == "" {
		if p.SourceTextHash != "" {
			return fmt.Errorf("evidence provenance source_text_hash is invalid")
		}
	} else {
		sum := sha256.Sum256([]byte(p.SourceText))
		if p.SourceTextHash != hex.EncodeToString(sum[:]) {
			return fmt.Errorf("evidence provenance source_text_hash does not match source_text")
		}
	}
	if p.MatchStatus != "exact" {
		if p.Start != nil || p.End != nil {
			return fmt.Errorf("evidence provenance non-exact reference has offsets")
		}
		return nil
	}
	if p.TextSpace != "raw_source_text" && p.TextSpace != "raw_text" {
		return fmt.Errorf("evidence provenance exact text_space is not original source text")
	}
	// Python offsets count Unicode code points, rather than UTF-8 bytes or UTF-16 units.
	source := []rune(p.SourceText)
	quoteHasText := strings.TrimFunc(p.Quote, func(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) }) != ""
	if p.Start == nil || p.End == nil || !quoteHasText || *p.Start < 0 || *p.End <= *p.Start || *p.End > len(source) {
		return fmt.Errorf("evidence provenance exact offsets are invalid")
	}
	if string(source[*p.Start:*p.End]) != p.Quote {
		return fmt.Errorf("evidence provenance exact quote does not match source_text")
	}
	if strings.Index(p.SourceText, p.Quote) != strings.LastIndex(p.SourceText, p.Quote) {
		return fmt.Errorf("evidence provenance exact quote is ambiguous")
	}
	return nil
}

func SafeEvidenceObjectKey(value string) bool {
	if strings.TrimSpace(value) == "" || strings.TrimSpace(value) != value || strings.HasPrefix(value, "/") || strings.ContainsAny(value, "\\\x00") {
		return false
	}
	for _, part := range strings.Split(value, "/") {
		if part == "." || part == ".." {
			return false
		}
	}
	return true
}

// EvidenceForDisplay gives JSON clients stable arrays without inferring evidence
// or changing any answer or writeback decision.
func EvidenceForDisplay(e *ManifestEvidence) ManifestEvidence {
	block := ManifestEvidence{Fields: []ManifestEvidenceField{}}
	if e == nil {
		return block
	}
	block.Summary = e.Summary
	for _, field := range e.Fields {
		if field.Reasons == nil {
			field.Reasons = []string{}
		}
		field.EvidenceRefs = append([]ManifestEvidenceRef{}, field.EvidenceRefs...)
		for i := range field.EvidenceRefs {
			ref := &field.EvidenceRefs[i]
			if ref.BBox == nil {
				ref.BBox = []float64{}
			}
			if ref.ProofAttachmentIDs == nil {
				ref.ProofAttachmentIDs = []string{}
			}
			if ref.ProofAttachments == nil {
				ref.ProofAttachments = []ManifestEvidenceAttachment{}
			}
		}
		block.Fields = append(block.Fields, field)
	}
	return block
}
