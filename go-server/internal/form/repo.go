package form

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

type FormFileRepo interface {
	Create(ctx context.Context, form FormFile) error
	GetByID(ctx context.Context, id uuid.UUID) (*FormFile, error)
	ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]FormFile, error)
}

type FillRunRepo interface {
	Create(ctx context.Context, run FillRun) error
	GetByID(ctx context.Context, id uuid.UUID) (*FillRun, error)
	ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int) ([]FillRun, error)
	ListByWorkspaceAndCreator(ctx context.Context, workspaceID uuid.UUID, createdBy uuid.UUID, status string, limit int, offset int) ([]FillRun, error)
	AttachJob(ctx context.Context, runID uuid.UUID, jobID uuid.UUID, queuedAt time.Time) error
	MarkRunning(ctx context.Context, runID uuid.UUID, startedAt time.Time) error
	MarkSucceeded(ctx context.Context, runID uuid.UUID, finishedAt time.Time, update FillRunCompletionUpdate) error
	MarkCompletedWithFailures(ctx context.Context, runID uuid.UUID, finishedAt time.Time, update FillRunCompletionUpdate, errMsg string) error
	MarkFailed(ctx context.Context, runID uuid.UUID, finishedAt time.Time, errMsg string) error
	RequestCancel(ctx context.Context, runID uuid.UUID, t time.Time) error
	MarkCanceled(ctx context.Context, runID uuid.UUID, finishedAt time.Time) error
	UpdateProgress(ctx context.Context, runID uuid.UUID, progressDone int, progressTotal int) error
}

type PGXFormFileRepo struct {
	pool *pgxpool.Pool
}

func NewPGXFormFileRepo(pool *pgxpool.Pool) *PGXFormFileRepo {
	return &PGXFormFileRepo{pool: pool}
}

func (r *PGXFormFileRepo) Create(ctx context.Context, form FormFile) error {
	if form.CreatedAt.IsZero() {
		form.CreatedAt = time.Now().UTC()
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO form_files (id, workspace_id, file_id, filename, created_by, created_at)
		VALUES ($1, $2, $3, $4, $5, $6)
	`, form.ID, form.WorkspaceID, form.FileID, form.Filename, form.CreatedBy, form.CreatedAt)
	return mapDBError(err, "form file already exists", "form file not found")
}

func (r *PGXFormFileRepo) GetByID(ctx context.Context, id uuid.UUID) (*FormFile, error) {
	return scanFormFile(r.pool.QueryRow(ctx, `
		SELECT id, workspace_id, file_id, filename, created_by, created_at
		FROM form_files WHERE id = $1
	`, id))
}

func (r *PGXFormFileRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]FormFile, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := r.pool.Query(ctx, `
		SELECT id, workspace_id, file_id, filename, created_by, created_at
		FROM form_files
		WHERE workspace_id = $1
		ORDER BY created_at DESC LIMIT $2 OFFSET $3
	`, workspaceID, limit, offset)
	if err != nil {
		return nil, mapDBError(err, "list form files conflict", "form files not found")
	}
	defer rows.Close()
	var forms []FormFile
	for rows.Next() {
		form, err := scanFormFile(rows)
		if err != nil {
			return nil, err
		}
		forms = append(forms, *form)
	}
	return forms, mapDBError(rows.Err(), "list form files conflict", "form files not found")
}

func scanFormFile(row pgx.Row) (*FormFile, error) {
	var item FormFile
	err := row.Scan(&item.ID, &item.WorkspaceID, &item.FileID, &item.Filename, &item.CreatedBy, &item.CreatedAt)
	if err != nil {
		return nil, mapDBError(err, "form file conflict", "form file not found")
	}
	return &item, nil
}

type PGXFillRunRepo struct {
	pool *pgxpool.Pool
}

func NewPGXFillRunRepo(pool *pgxpool.Pool) *PGXFillRunRepo {
	return &PGXFillRunRepo{pool: pool}
}

func (r *PGXFillRunRepo) Create(ctx context.Context, run FillRun) error {
	now := time.Now().UTC()
	if run.CreatedAt.IsZero() {
		run.CreatedAt = now
	}
	if run.UpdatedAt.IsZero() {
		run.UpdatedAt = now
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO fill_runs (
			id, workspace_id, form_file_id, job_id, name, knowledge_base_id, index_version_id,
			target_namespace, global_namespace, room_context, rows_spec, retrieval_mode, prompt_version,
			judge_enabled, use_judge_cache, writeback_enabled, status, progress_total, progress_done,
			out_dir, run_manifest_path, summary_path, filled_form_artifact_id,
			answered_count, partial_clue_count, not_found_count, conflict_unresolved_count,
			review_required_count, writeback_allowed_count, failed_count, error_message,
			created_by, created_at, queued_at, started_at, finished_at, updated_at
		)
		VALUES (
			$1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
			$13, $14, $15, $16, $17, $18, $19, $20, $21, $22,
			$23, $24, $25, $26, $27, $28, $29, $30, $31, $32, $33, $34, $35, $36, $37
		)
	`, run.ID, run.WorkspaceID, run.FormFileID, run.JobID, run.Name, run.KnowledgeBaseID, run.IndexVersionID,
		run.TargetNamespace, run.GlobalNamespace, run.RoomContext, run.RowsSpec, run.RetrievalMode, run.PromptVersion,
		run.JudgeEnabled, run.UseJudgeCache, run.WritebackEnabled, run.Status, run.ProgressTotal, run.ProgressDone,
		run.OutDir, run.RunManifestPath, run.SummaryPath, run.FilledFormArtifactID,
		run.AnsweredCount, run.PartialClueCount, run.NotFoundCount, run.ConflictUnresolvedCount,
		run.ReviewRequiredCount, run.WritebackAllowedCount, run.FailedCount, run.ErrorMessage,
		run.CreatedBy, run.CreatedAt, run.QueuedAt, run.StartedAt, run.FinishedAt, run.UpdatedAt)
	return mapDBError(err, "fill run already exists", "fill run not found")
}

func (r *PGXFillRunRepo) GetByID(ctx context.Context, id uuid.UUID) (*FillRun, error) {
	return scanFillRun(r.pool.QueryRow(ctx, selectFillRunSQL()+` WHERE id = $1`, id))
}

func (r *PGXFillRunRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, status string, limit int, offset int) ([]FillRun, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	var rows pgx.Rows
	var err error
	if status != "" {
		rows, err = r.pool.Query(ctx, selectFillRunSQL()+` WHERE workspace_id = $1 AND status = $2 ORDER BY created_at DESC LIMIT $3 OFFSET $4`, workspaceID, status, limit, offset)
	} else {
		rows, err = r.pool.Query(ctx, selectFillRunSQL()+` WHERE workspace_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3`, workspaceID, limit, offset)
	}
	if err != nil {
		return nil, mapDBError(err, "list fill runs conflict", "fill runs not found")
	}
	defer rows.Close()
	var runs []FillRun
	for rows.Next() {
		run, err := scanFillRun(rows)
		if err != nil {
			return nil, err
		}
		runs = append(runs, *run)
	}
	return runs, mapDBError(rows.Err(), "list fill runs conflict", "fill runs not found")
}

func (r *PGXFillRunRepo) ListByWorkspaceAndCreator(ctx context.Context, workspaceID uuid.UUID, createdBy uuid.UUID, status string, limit int, offset int) ([]FillRun, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	var rows pgx.Rows
	var err error
	if status != "" {
		rows, err = r.pool.Query(ctx, selectFillRunSQL()+` WHERE workspace_id = $1 AND created_by = $2 AND status = $3 ORDER BY created_at DESC LIMIT $4 OFFSET $5`, workspaceID, createdBy, status, limit, offset)
	} else {
		rows, err = r.pool.Query(ctx, selectFillRunSQL()+` WHERE workspace_id = $1 AND created_by = $2 ORDER BY created_at DESC LIMIT $3 OFFSET $4`, workspaceID, createdBy, limit, offset)
	}
	if err != nil {
		return nil, mapDBError(err, "list fill runs conflict", "fill runs not found")
	}
	defer rows.Close()
	var runs []FillRun
	for rows.Next() {
		run, err := scanFillRun(rows)
		if err != nil {
			return nil, err
		}
		runs = append(runs, *run)
	}
	return runs, mapDBError(rows.Err(), "list fill runs conflict", "fill runs not found")
}

func (r *PGXFillRunRepo) AttachJob(ctx context.Context, runID uuid.UUID, jobID uuid.UUID, queuedAt time.Time) error {
	return r.updateStatus(ctx, runID, []string{FillRunStatusCreated}, FillRunStatusQueued, `
		job_id = $4, queued_at = $5, updated_at = now()
	`, jobID, queuedAt)
}

func (r *PGXFillRunRepo) MarkRunning(ctx context.Context, runID uuid.UUID, startedAt time.Time) error {
	return r.updateStatus(ctx, runID, []string{FillRunStatusQueued, FillRunStatusCreated}, FillRunStatusRunning, `
		started_at = COALESCE(started_at, $4), updated_at = now()
	`, startedAt)
}

func (r *PGXFillRunRepo) MarkSucceeded(ctx context.Context, runID uuid.UUID, finishedAt time.Time, update FillRunCompletionUpdate) error {
	return r.markCompleted(ctx, runID, FillRunStatusSucceeded, finishedAt, update, "")
}

func (r *PGXFillRunRepo) MarkCompletedWithFailures(ctx context.Context, runID uuid.UUID, finishedAt time.Time, update FillRunCompletionUpdate, errMsg string) error {
	return r.markCompleted(ctx, runID, FillRunStatusCompletedWithFailures, finishedAt, update, errMsg)
}

func (r *PGXFillRunRepo) MarkFailed(ctx context.Context, runID uuid.UUID, finishedAt time.Time, errMsg string) error {
	return r.updateStatus(ctx, runID, []string{FillRunStatusCreated, FillRunStatusQueued, FillRunStatusRunning, FillRunStatusCancelRequested}, FillRunStatusFailed, `
		finished_at = $4, error_message = $5, updated_at = now()
	`, finishedAt, errMsg)
}

func (r *PGXFillRunRepo) RequestCancel(ctx context.Context, runID uuid.UUID, t time.Time) error {
	_ = t
	return r.updateStatus(ctx, runID, []string{FillRunStatusRunning}, FillRunStatusCancelRequested, `updated_at = now()`)
}

func (r *PGXFillRunRepo) MarkCanceled(ctx context.Context, runID uuid.UUID, finishedAt time.Time) error {
	return r.updateStatus(ctx, runID, []string{FillRunStatusCreated, FillRunStatusQueued, FillRunStatusRunning, FillRunStatusCancelRequested}, FillRunStatusCanceled, `
		finished_at = $4, updated_at = now()
	`, finishedAt)
}

func (r *PGXFillRunRepo) UpdateProgress(ctx context.Context, runID uuid.UUID, progressDone int, progressTotal int) error {
	tag, err := r.pool.Exec(ctx, `
		UPDATE fill_runs
		SET progress_done = $2, progress_total = $3, updated_at = now()
		WHERE id = $1
	`, runID, progressDone, progressTotal)
	if err != nil {
		return mapDBError(err, "update fill run progress conflict", "fill run not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "fill run not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXFillRunRepo) markCompleted(ctx context.Context, runID uuid.UUID, status string, finishedAt time.Time, update FillRunCompletionUpdate, errMsg string) error {
	return r.updateStatus(ctx, runID, []string{FillRunStatusRunning, FillRunStatusQueued, FillRunStatusCancelRequested}, status, `
		finished_at = $4, run_manifest_path = $5, summary_path = $6, filled_form_artifact_id = $7,
		progress_total = $8, progress_done = $9,
		answered_count = $10, partial_clue_count = $11, not_found_count = $12,
		conflict_unresolved_count = $13, review_required_count = $14, writeback_allowed_count = $15,
		failed_count = $16, error_message = $17, updated_at = now()
	`, finishedAt, update.RunManifestPath, update.SummaryPath, update.FilledFormArtifactID,
		update.ProgressTotal, update.ProgressDone, update.AnsweredCount, update.PartialClueCount,
		update.NotFoundCount, update.ConflictUnresolvedCount, update.ReviewRequiredCount,
		update.WritebackAllowedCount, update.FailedCount, errMsg)
}

func (r *PGXFillRunRepo) updateStatus(ctx context.Context, runID uuid.UUID, from []string, to string, setClause string, args ...any) error {
	queryArgs := []any{runID, from, to}
	queryArgs = append(queryArgs, args...)
	tag, err := r.pool.Exec(ctx, `UPDATE fill_runs SET status = $3, `+setClause+` WHERE id = $1 AND status = ANY($2)`, queryArgs...)
	if err != nil {
		return mapDBError(err, "update fill run conflict", "fill run not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeConflict, "fill run status changed", http.StatusConflict, nil, nil)
	}
	return nil
}

func selectFillRunSQL() string {
	return `
		SELECT id, workspace_id, form_file_id, job_id, COALESCE(name, ''), knowledge_base_id, index_version_id,
			target_namespace, global_namespace, COALESCE(room_context, ''), rows_spec, retrieval_mode, prompt_version,
			judge_enabled, use_judge_cache, writeback_enabled, status, progress_total, progress_done,
			COALESCE(out_dir, ''), COALESCE(run_manifest_path, ''), COALESCE(summary_path, ''), filled_form_artifact_id,
			answered_count, partial_clue_count, not_found_count, conflict_unresolved_count,
			review_required_count, writeback_allowed_count, failed_count, COALESCE(error_message, ''),
			created_by, created_at, queued_at, started_at, finished_at, updated_at
		FROM fill_runs`
}

func scanFillRun(row pgx.Row) (*FillRun, error) {
	var run FillRun
	err := row.Scan(
		&run.ID, &run.WorkspaceID, &run.FormFileID, &run.JobID, &run.Name, &run.KnowledgeBaseID, &run.IndexVersionID,
		&run.TargetNamespace, &run.GlobalNamespace, &run.RoomContext, &run.RowsSpec, &run.RetrievalMode, &run.PromptVersion,
		&run.JudgeEnabled, &run.UseJudgeCache, &run.WritebackEnabled, &run.Status, &run.ProgressTotal, &run.ProgressDone,
		&run.OutDir, &run.RunManifestPath, &run.SummaryPath, &run.FilledFormArtifactID,
		&run.AnsweredCount, &run.PartialClueCount, &run.NotFoundCount, &run.ConflictUnresolvedCount,
		&run.ReviewRequiredCount, &run.WritebackAllowedCount, &run.FailedCount, &run.ErrorMessage,
		&run.CreatedBy, &run.CreatedAt, &run.QueuedAt, &run.StartedAt, &run.FinishedAt, &run.UpdatedAt,
	)
	if err != nil {
		return nil, mapDBError(err, "fill run conflict", "fill run not found")
	}
	return &run, nil
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
			return httpx.NewAppError(httpx.CodeNotFound, notFoundMessage, http.StatusNotFound, nil, err)
		}
	}
	return httpx.NewAppError(httpx.CodeInternal, "database operation failed", http.StatusInternalServerError, nil, err)
}
