package review

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Repo interface {
	Create(ctx context.Context, item ReviewItem) error
	UpsertByRunAndField(ctx context.Context, item ReviewItem) error
	GetByID(ctx context.Context, id uuid.UUID) (*ReviewItem, error)
	ListByRun(ctx context.Context, runID uuid.UUID, filter ReviewFilter) ([]ReviewItem, error)
	CountByRun(ctx context.Context, runID uuid.UUID) (ReviewCounts, error)
	UpdateStatus(ctx context.Context, id uuid.UUID, update ReviewStatusUpdate) error
	DeleteByRun(ctx context.Context, runID uuid.UUID) error
}

type PGXRepo struct {
	pool *pgxpool.Pool
}

func NewPGXRepo(pool *pgxpool.Pool) *PGXRepo {
	return &PGXRepo{pool: pool}
}

func (r *PGXRepo) Create(ctx context.Context, item ReviewItem) error {
	normalizeReviewItem(&item)
	_, err := r.pool.Exec(ctx, insertReviewSQL(),
		item.ID, item.WorkspaceID, item.RunID, nullableString(item.FieldID), item.RowIndex, nullableString(item.TargetCell), nullableString(item.QuestionText),
		nullableString(item.AnswerStatus), nullableString(item.AnswerValue), item.Confidence,
		mustJSON(item.SourceChunkIDs), mustJSON(item.EvidenceAttachmentIDs), mustJSON(item.ReferenceChunkIDs), mustJSON(item.ReferenceSourceDocuments), mustJSON(item.ReferenceSnippets),
		mustJSON(item.CriticFlags), item.RiskLevel, item.ReviewRequired, item.WritebackAllowed,
		nullableString(item.SuggestedStatus), nullableString(item.SuggestedAnswerValue), mustJSON(item.SuggestedReferenceSourceDocuments), mustJSON(item.Reasons),
		nullableString(item.WritebackStatus), nullableString(item.WritebackAction), mustJSON(item.EvidenceRefs), nullableString(item.WritebackErrorCode),
		item.Status, item.ReviewerID, item.ReviewedAt, nullableString(item.ReviewComment), nullableString(item.EditedAnswer),
		mustJSON(item.RawPayload), mustJSON(item.OverlayPayload), item.CreatedAt, item.UpdatedAt,
	)
	return mapDBError(err, "review item already exists", "review item not found")
}

func (r *PGXRepo) UpsertByRunAndField(ctx context.Context, item ReviewItem) error {
	normalizeReviewItem(&item)
	existingID, err := r.findExistingID(ctx, item)
	if err != nil && !isNotFound(err) {
		return err
	}
	if existingID == uuid.Nil {
		return r.Create(ctx, item)
	}
	tag, err := r.pool.Exec(ctx, `
		UPDATE review_items
		SET workspace_id = $2, field_id = $3, row_index = $4, target_cell = $5, question_text = $6,
			answer_status = $7, answer_value = $8, confidence = $9,
			source_chunk_ids = $10, evidence_attachment_ids = $11, reference_chunk_ids = $12,
			reference_source_documents = $13, reference_snippets = $14, critic_flags = $15,
				risk_level = $16, review_required = $17, writeback_allowed = $18,
				suggested_status = $19, suggested_answer_value = $20,
				suggested_reference_source_documents = $21, reasons = $22,
				writeback_status = $23, writeback_action = $24, evidence_refs = $25,
				writeback_error_code = $26, raw_payload = $27, overlay_payload = $28, updated_at = now()
			WHERE id = $1
		`, existingID, item.WorkspaceID, nullableString(item.FieldID), item.RowIndex, nullableString(item.TargetCell), nullableString(item.QuestionText),
		nullableString(item.AnswerStatus), nullableString(item.AnswerValue), item.Confidence,
		mustJSON(item.SourceChunkIDs), mustJSON(item.EvidenceAttachmentIDs), mustJSON(item.ReferenceChunkIDs),
		mustJSON(item.ReferenceSourceDocuments), mustJSON(item.ReferenceSnippets), mustJSON(item.CriticFlags),
		item.RiskLevel, item.ReviewRequired, item.WritebackAllowed, nullableString(item.SuggestedStatus),
		nullableString(item.SuggestedAnswerValue), mustJSON(item.SuggestedReferenceSourceDocuments), mustJSON(item.Reasons),
		nullableString(item.WritebackStatus), nullableString(item.WritebackAction), mustJSON(item.EvidenceRefs),
		nullableString(item.WritebackErrorCode), mustJSON(item.RawPayload), mustJSON(item.OverlayPayload))
	if err != nil {
		return mapDBError(err, "upsert review item conflict", "review item not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "review item not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXRepo) GetByID(ctx context.Context, id uuid.UUID) (*ReviewItem, error) {
	return scanReviewItem(r.pool.QueryRow(ctx, selectReviewSQL()+` WHERE id = $1`, id))
}

func (r *PGXRepo) ListByRun(ctx context.Context, runID uuid.UUID, filter ReviewFilter) ([]ReviewItem, error) {
	if filter.Limit <= 0 || filter.Limit > 200 {
		filter.Limit = 50
	}
	clauses := []string{"run_id = $1"}
	args := []any{runID}
	if filter.WorkspaceID != uuid.Nil {
		args = append(args, filter.WorkspaceID)
		clauses = append(clauses, "workspace_id = $"+strconv.Itoa(len(args)))
	}
	if strings.TrimSpace(filter.Status) != "" {
		args = append(args, strings.TrimSpace(filter.Status))
		clauses = append(clauses, "status = $"+strconv.Itoa(len(args)))
	}
	if strings.TrimSpace(filter.RiskLevel) != "" {
		args = append(args, strings.TrimSpace(filter.RiskLevel))
		clauses = append(clauses, "risk_level = $"+strconv.Itoa(len(args)))
	}
	if filter.ReviewRequired != nil {
		args = append(args, *filter.ReviewRequired)
		clauses = append(clauses, "review_required = $"+strconv.Itoa(len(args)))
	}
	if filter.WritebackAllowed != nil {
		args = append(args, *filter.WritebackAllowed)
		clauses = append(clauses, "writeback_allowed = $"+strconv.Itoa(len(args)))
	}
	args = append(args, filter.Limit, filter.Offset)
	query := selectReviewSQL() + ` WHERE ` + strings.Join(clauses, " AND ") + ` ORDER BY row_index ASC NULLS LAST, target_cell ASC NULLS LAST, created_at ASC LIMIT $` + strconv.Itoa(len(args)-1) + ` OFFSET $` + strconv.Itoa(len(args))
	rows, err := r.pool.Query(ctx, query, args...)
	if err != nil {
		return nil, mapDBError(err, "list review items conflict", "review items not found")
	}
	defer rows.Close()
	var out []ReviewItem
	for rows.Next() {
		item, err := scanReviewItem(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *item)
	}
	return out, mapDBError(rows.Err(), "list review items conflict", "review items not found")
}

func (r *PGXRepo) CountByRun(ctx context.Context, runID uuid.UUID) (ReviewCounts, error) {
	var counts ReviewCounts
	err := r.pool.QueryRow(ctx, `
		SELECT
			COUNT(*),
			COUNT(*) FILTER (WHERE status = 'pending'),
			COUNT(*) FILTER (WHERE status = 'approved'),
			COUNT(*) FILTER (WHERE status = 'rejected'),
			COUNT(*) FILTER (WHERE status = 'edited'),
			COUNT(*) FILTER (WHERE status = 'ignored'),
			COUNT(*) FILTER (WHERE status = 'reopened'),
			COUNT(*) FILTER (WHERE risk_level = 'high'),
			COUNT(*) FILTER (WHERE review_required),
			COUNT(*) FILTER (WHERE writeback_allowed)
		FROM review_items
		WHERE run_id = $1
	`, runID).Scan(&counts.Total, &counts.Pending, &counts.Approved, &counts.Rejected, &counts.Edited, &counts.Ignored, &counts.Reopened, &counts.HighRisk, &counts.ReviewRequired, &counts.WritebackAllowed)
	return counts, mapDBError(err, "count review items conflict", "review items not found")
}

func (r *PGXRepo) UpdateStatus(ctx context.Context, id uuid.UUID, update ReviewStatusUpdate) error {
	if !ValidReviewStatus(update.Status) {
		return httpx.NewAppError(httpx.CodeInvalidArgument, "invalid review status", http.StatusBadRequest, map[string]string{"status": update.Status}, nil)
	}
	if update.ReviewedAt.IsZero() {
		update.ReviewedAt = time.Now().UTC()
	}
	tag, err := r.pool.Exec(ctx, `
		UPDATE review_items
		SET status = $2, reviewer_id = $3, reviewed_at = $4, review_comment = $5,
			edited_answer = $6, updated_at = now()
		WHERE id = $1
	`, id, update.Status, update.ReviewerID, update.ReviewedAt, nullableString(update.ReviewComment), nullableString(update.EditedAnswer))
	if err != nil {
		return mapDBError(err, "update review item status conflict", "review item not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "review item not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXRepo) DeleteByRun(ctx context.Context, runID uuid.UUID) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM review_items WHERE run_id = $1`, runID)
	return mapDBError(err, "delete review items conflict", "review items not found")
}

func (r *PGXRepo) findExistingID(ctx context.Context, item ReviewItem) (uuid.UUID, error) {
	var id uuid.UUID
	if strings.TrimSpace(item.FieldID) != "" {
		err := r.pool.QueryRow(ctx, `SELECT id FROM review_items WHERE run_id = $1 AND field_id = $2 LIMIT 1`, item.RunID, item.FieldID).Scan(&id)
		return id, mapDBError(err, "find review item conflict", "review item not found")
	}
	err := r.pool.QueryRow(ctx, `
		SELECT id FROM review_items
		WHERE run_id = $1 AND row_index = $2 AND COALESCE(target_cell, '') = $3
		LIMIT 1
	`, item.RunID, item.RowIndex, item.TargetCell).Scan(&id)
	return id, mapDBError(err, "find review item conflict", "review item not found")
}

func insertReviewSQL() string {
	return `
		INSERT INTO review_items (
			id, workspace_id, run_id, field_id, row_index, target_cell, question_text,
			answer_status, answer_value, confidence, source_chunk_ids, evidence_attachment_ids,
			reference_chunk_ids, reference_source_documents, reference_snippets, critic_flags,
			risk_level, review_required, writeback_allowed, suggested_status, suggested_answer_value,
			suggested_reference_source_documents, reasons, writeback_status, writeback_action,
			evidence_refs, writeback_error_code, status, reviewer_id, reviewed_at,
			review_comment, edited_answer, raw_payload, overlay_payload, created_at, updated_at
		)
		VALUES (
			$1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
			$17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31, $32,
			$33, $34, $35, $36
		)
	`
}

func selectReviewSQL() string {
	return `
		SELECT id, workspace_id, run_id, COALESCE(field_id, ''), COALESCE(row_index, 0),
			COALESCE(target_cell, ''), COALESCE(question_text, ''), COALESCE(answer_status, ''),
			COALESCE(answer_value, ''), COALESCE(confidence, 0), source_chunk_ids,
			evidence_attachment_ids, reference_chunk_ids, reference_source_documents,
			reference_snippets, critic_flags, risk_level, review_required, writeback_allowed,
			COALESCE(suggested_status, ''), COALESCE(suggested_answer_value, ''),
			suggested_reference_source_documents, reasons, COALESCE(writeback_status, ''),
			COALESCE(writeback_action, ''), evidence_refs, COALESCE(writeback_error_code, ''),
			status, reviewer_id, reviewed_at,
			COALESCE(review_comment, ''), COALESCE(edited_answer, ''), raw_payload,
			overlay_payload, created_at, updated_at
		FROM review_items`
}

func scanReviewItem(row pgx.Row) (*ReviewItem, error) {
	var item ReviewItem
	var sourceChunkIDs, evidenceAttachmentIDs, referenceChunkIDs, referenceSourceDocuments, referenceSnippets []byte
	var criticFlags, suggestedReferenceSourceDocuments, reasons, evidenceRefs, rawPayload, overlayPayload []byte
	err := row.Scan(
		&item.ID, &item.WorkspaceID, &item.RunID, &item.FieldID, &item.RowIndex, &item.TargetCell,
		&item.QuestionText, &item.AnswerStatus, &item.AnswerValue, &item.Confidence,
		&sourceChunkIDs, &evidenceAttachmentIDs, &referenceChunkIDs, &referenceSourceDocuments,
		&referenceSnippets, &criticFlags, &item.RiskLevel, &item.ReviewRequired,
		&item.WritebackAllowed, &item.SuggestedStatus, &item.SuggestedAnswerValue,
		&suggestedReferenceSourceDocuments, &reasons, &item.WritebackStatus, &item.WritebackAction,
		&evidenceRefs, &item.WritebackErrorCode, &item.Status, &item.ReviewerID,
		&item.ReviewedAt, &item.ReviewComment, &item.EditedAnswer, &rawPayload,
		&overlayPayload, &item.CreatedAt, &item.UpdatedAt,
	)
	if err != nil {
		return nil, mapDBError(err, "review item conflict", "review item not found")
	}
	_ = json.Unmarshal(sourceChunkIDs, &item.SourceChunkIDs)
	_ = json.Unmarshal(evidenceAttachmentIDs, &item.EvidenceAttachmentIDs)
	_ = json.Unmarshal(referenceChunkIDs, &item.ReferenceChunkIDs)
	_ = json.Unmarshal(referenceSourceDocuments, &item.ReferenceSourceDocuments)
	_ = json.Unmarshal(referenceSnippets, &item.ReferenceSnippets)
	_ = json.Unmarshal(criticFlags, &item.CriticFlags)
	_ = json.Unmarshal(suggestedReferenceSourceDocuments, &item.SuggestedReferenceSourceDocuments)
	_ = json.Unmarshal(reasons, &item.Reasons)
	_ = json.Unmarshal(evidenceRefs, &item.EvidenceRefs)
	_ = json.Unmarshal(rawPayload, &item.RawPayload)
	_ = json.Unmarshal(overlayPayload, &item.OverlayPayload)
	normalizeReviewItem(&item)
	return &item, nil
}

func normalizeReviewItem(item *ReviewItem) {
	if item.ID == uuid.Nil {
		item.ID = uuid.New()
	}
	if item.RiskLevel == "" {
		item.RiskLevel = ReviewRiskMedium
	}
	if item.Status == "" {
		if item.ReviewRequired {
			item.Status = ReviewStatusPending
		} else {
			item.Status = ReviewStatusIgnored
		}
	}
	now := time.Now().UTC()
	if item.CreatedAt.IsZero() {
		item.CreatedAt = now
	}
	if item.UpdatedAt.IsZero() {
		item.UpdatedAt = now
	}
	if item.SourceChunkIDs == nil {
		item.SourceChunkIDs = []string{}
	}
	if item.EvidenceAttachmentIDs == nil {
		item.EvidenceAttachmentIDs = []string{}
	}
	if item.ReferenceChunkIDs == nil {
		item.ReferenceChunkIDs = []string{}
	}
	if item.ReferenceSourceDocuments == nil {
		item.ReferenceSourceDocuments = []map[string]any{}
	}
	if item.ReferenceSnippets == nil {
		item.ReferenceSnippets = []string{}
	}
	if item.CriticFlags == nil {
		item.CriticFlags = []string{}
	}
	if item.SuggestedReferenceSourceDocuments == nil {
		item.SuggestedReferenceSourceDocuments = []map[string]any{}
	}
	if item.Reasons == nil {
		item.Reasons = []string{}
	}
	if item.EvidenceRefs == nil {
		item.EvidenceRefs = []map[string]any{}
	}
	if item.RawPayload == nil {
		item.RawPayload = map[string]any{}
	}
	if item.OverlayPayload == nil {
		item.OverlayPayload = map[string]any{}
	}
}

func mustJSON(value any) []byte {
	data, err := json.Marshal(value)
	if err != nil {
		return []byte("null")
	}
	return data
}

func nullableString(value string) any {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil
	}
	return value
}

func isNotFound(err error) bool {
	var appErr *httpx.AppError
	return errors.As(err, &appErr) && appErr.Code == httpx.CodeNotFound
}

func mapDBError(err error, conflictMessage string, notFoundMessage string) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return httpx.NewAppError(httpx.CodeNotFound, notFoundMessage, http.StatusNotFound, nil, err)
	}
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		switch pgErr.Code {
		case "23505":
			return httpx.NewAppError(httpx.CodeConflict, conflictMessage, http.StatusConflict, nil, err)
		case "23503":
			return httpx.NewAppError(httpx.CodeInvalidArgument, notFoundMessage, http.StatusBadRequest, nil, err)
		}
	}
	return httpx.NewAppError(httpx.CodeInternal, "database operation failed", http.StatusInternalServerError, nil, err)
}
