package knowledge

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

type KnowledgeBaseRepo interface {
	Create(ctx context.Context, kb KnowledgeBase) error
	GetByID(ctx context.Context, id uuid.UUID) (*KnowledgeBase, error)
	ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]KnowledgeBase, error)
	ListOptionsByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]KnowledgeBase, error)
	ListReadyOptionsByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]KnowledgeBase, error)
	UpdateCurrentIndexVersion(ctx context.Context, kbID uuid.UUID, versionID uuid.UUID) error
	UpdateStatus(ctx context.Context, kbID uuid.UUID, status string) error
	Update(ctx context.Context, kb KnowledgeBase) error
	Delete(ctx context.Context, id uuid.UUID) error
}

type KnowledgeDocumentRepo interface {
	Create(ctx context.Context, doc KnowledgeDocument) error
	GetByID(ctx context.Context, id uuid.UUID) (*KnowledgeDocument, error)
	ListByKnowledgeBase(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int) ([]KnowledgeDocument, error)
	ListActiveByKnowledgeBase(ctx context.Context, kbID uuid.UUID) ([]KnowledgeDocument, error)
	MarkStatus(ctx context.Context, id uuid.UUID, status string, errMsg string) error
	SoftDelete(ctx context.Context, id uuid.UUID) error
}

type KnowledgeIndexVersionRepo interface {
	Create(ctx context.Context, version KnowledgeIndexVersion) error
	GetByID(ctx context.Context, id uuid.UUID) (*KnowledgeIndexVersion, error)
	ListByKnowledgeBase(ctx context.Context, kbID uuid.UUID, limit int, offset int) ([]KnowledgeIndexVersion, error)
	NextVersion(ctx context.Context, kbID uuid.UUID) (int, error)
	MarkReady(ctx context.Context, id uuid.UUID, artifactDir string, manifestPath string, documentCount int, chunkCount int, readyAt time.Time) error
	MarkFailed(ctx context.Context, id uuid.UUID, errMsg string, failedAt time.Time) error
	ArchiveOldVersions(ctx context.Context, kbID uuid.UUID, exceptID uuid.UUID) error
}

type IngestionJobRepo interface {
	Create(ctx context.Context, job IngestionJob) error
	GetByID(ctx context.Context, id uuid.UUID) (*IngestionJob, error)
	ListByKnowledgeBase(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int) ([]IngestionJob, error)
	AttachJob(ctx context.Context, ingestionJobID uuid.UUID, jobID uuid.UUID, queuedAt time.Time) error
	MarkRunning(ctx context.Context, id uuid.UUID, startedAt time.Time) error
	MarkSucceeded(ctx context.Context, id uuid.UUID, finishedAt time.Time, progress int) error
	MarkFailed(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error
	RequestCancel(ctx context.Context, id uuid.UUID, t time.Time) error
	MarkCanceled(ctx context.Context, id uuid.UUID, finishedAt time.Time) error
	UpdateProgress(ctx context.Context, id uuid.UUID, progress int) error
}

type PGXKnowledgeBaseRepo struct {
	pool *pgxpool.Pool
}

func NewPGXKnowledgeBaseRepo(pool *pgxpool.Pool) *PGXKnowledgeBaseRepo {
	return &PGXKnowledgeBaseRepo{pool: pool}
}

func (r *PGXKnowledgeBaseRepo) Create(ctx context.Context, kb KnowledgeBase) error {
	now := time.Now().UTC()
	if kb.CreatedAt.IsZero() {
		kb.CreatedAt = now
	}
	if kb.UpdatedAt.IsZero() {
		kb.UpdatedAt = now
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO knowledge_bases (
			id, workspace_id, name, namespace, description, qdrant_collection, current_index_version_id,
			status, document_count, last_ingested_at, created_by, created_at, updated_at
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
	`, kb.ID, kb.WorkspaceID, kb.Name, kb.Namespace, kb.Description, kb.QdrantCollection, kb.CurrentIndexVersionID,
		kb.Status, kb.DocumentCount, kb.LastIngestedAt, kb.CreatedBy, kb.CreatedAt, kb.UpdatedAt)
	return mapDBError(err, "knowledge base already exists", "knowledge base not found")
}

func (r *PGXKnowledgeBaseRepo) GetByID(ctx context.Context, id uuid.UUID) (*KnowledgeBase, error) {
	return scanKnowledgeBase(r.pool.QueryRow(ctx, selectKnowledgeBaseSQL()+` WHERE id = $1`, id))
}

func (r *PGXKnowledgeBaseRepo) ListByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]KnowledgeBase, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := r.pool.Query(ctx, selectKnowledgeBaseSQL()+` WHERE workspace_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3`, workspaceID, limit, offset)
	if err != nil {
		return nil, mapDBError(err, "list knowledge bases conflict", "knowledge bases not found")
	}
	defer rows.Close()
	var out []KnowledgeBase
	for rows.Next() {
		item, err := scanKnowledgeBase(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *item)
	}
	return out, mapDBError(rows.Err(), "list knowledge bases conflict", "knowledge bases not found")
}

func (r *PGXKnowledgeBaseRepo) ListOptionsByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]KnowledgeBase, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := r.pool.Query(ctx, selectKnowledgeBaseSQL()+` WHERE workspace_id = $1 AND status <> $2 ORDER BY name ASC LIMIT $3 OFFSET $4`, workspaceID, KnowledgeBaseStatusArchived, limit, offset)
	if err != nil {
		return nil, mapDBError(err, "list knowledge base options conflict", "knowledge bases not found")
	}
	defer rows.Close()
	var out []KnowledgeBase
	for rows.Next() {
		item, err := scanKnowledgeBase(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *item)
	}
	return out, mapDBError(rows.Err(), "list knowledge base options conflict", "knowledge bases not found")
}

func (r *PGXKnowledgeBaseRepo) ListReadyOptionsByWorkspace(ctx context.Context, workspaceID uuid.UUID, limit int, offset int) ([]KnowledgeBase, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := r.pool.Query(ctx, selectKnowledgeBaseSQL()+` WHERE workspace_id = $1 AND status = $2 ORDER BY name ASC LIMIT $3 OFFSET $4`, workspaceID, KnowledgeBaseStatusReady, limit, offset)
	if err != nil {
		return nil, mapDBError(err, "list ready knowledge base options conflict", "knowledge bases not found")
	}
	defer rows.Close()
	var out []KnowledgeBase
	for rows.Next() {
		item, err := scanKnowledgeBase(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *item)
	}
	return out, mapDBError(rows.Err(), "list ready knowledge base options conflict", "knowledge bases not found")
}

func (r *PGXKnowledgeBaseRepo) UpdateCurrentIndexVersion(ctx context.Context, kbID uuid.UUID, versionID uuid.UUID) error {
	tag, err := r.pool.Exec(ctx, `
		UPDATE knowledge_bases SET current_index_version_id = $2, status = $3, last_ingested_at = now(), updated_at = now() WHERE id = $1
	`, kbID, versionID, KnowledgeBaseStatusReady)
	if err != nil {
		return mapDBError(err, "update current index version conflict", "knowledge base not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge base not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXKnowledgeBaseRepo) UpdateStatus(ctx context.Context, kbID uuid.UUID, status string) error {
	tag, err := r.pool.Exec(ctx, `UPDATE knowledge_bases SET status = $2, updated_at = now() WHERE id = $1`, kbID, status)
	if err != nil {
		return mapDBError(err, "update knowledge base status conflict", "knowledge base not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge base not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXKnowledgeBaseRepo) Update(ctx context.Context, kb KnowledgeBase) error {
	tag, err := r.pool.Exec(ctx, `
		UPDATE knowledge_bases
		SET name = $2, namespace = $3, description = $4, qdrant_collection = $5,
			current_index_version_id = $6, status = $7, document_count = $8,
			last_ingested_at = $9, updated_at = now()
		WHERE id = $1
	`, kb.ID, kb.Name, kb.Namespace, kb.Description, kb.QdrantCollection, kb.CurrentIndexVersionID,
		kb.Status, kb.DocumentCount, kb.LastIngestedAt)
	if err != nil {
		return mapDBError(err, "update knowledge base conflict", "knowledge base not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge base not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXKnowledgeBaseRepo) Delete(ctx context.Context, id uuid.UUID) error {
	tag, err := r.pool.Exec(ctx, `DELETE FROM knowledge_bases WHERE id = $1`, id)
	if err != nil {
		return mapDBError(err, "delete knowledge base conflict", "knowledge base not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge base not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func selectKnowledgeBaseSQL() string {
	return `
		SELECT id, workspace_id, name, COALESCE(namespace, ''), COALESCE(description, ''), COALESCE(qdrant_collection, ''),
			current_index_version_id, COALESCE(status, 'empty'),
			(
				SELECT COUNT(*)
				FROM knowledge_documents
				WHERE knowledge_documents.knowledge_base_id = knowledge_bases.id
					AND knowledge_documents.status <> 'deleted'
			)::INT AS document_count,
			last_ingested_at, created_by, created_at, updated_at
		FROM knowledge_bases`
}

func scanKnowledgeBase(row pgx.Row) (*KnowledgeBase, error) {
	var kb KnowledgeBase
	err := row.Scan(&kb.ID, &kb.WorkspaceID, &kb.Name, &kb.Namespace, &kb.Description, &kb.QdrantCollection, &kb.CurrentIndexVersionID,
		&kb.Status, &kb.DocumentCount, &kb.LastIngestedAt, &kb.CreatedBy, &kb.CreatedAt, &kb.UpdatedAt)
	if err != nil {
		return nil, mapDBError(err, "knowledge base conflict", "knowledge base not found")
	}
	return &kb, nil
}

type PGXKnowledgeDocumentRepo struct {
	pool *pgxpool.Pool
}

func NewPGXKnowledgeDocumentRepo(pool *pgxpool.Pool) *PGXKnowledgeDocumentRepo {
	return &PGXKnowledgeDocumentRepo{pool: pool}
}

func (r *PGXKnowledgeDocumentRepo) Create(ctx context.Context, doc KnowledgeDocument) error {
	now := time.Now().UTC()
	if doc.CreatedAt.IsZero() {
		doc.CreatedAt = now
	}
	if doc.UpdatedAt.IsZero() {
		doc.UpdatedAt = now
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO knowledge_documents (
			id, knowledge_base_id, workspace_id, file_id, filename, document_role, namespace,
			status, created_by, created_at, updated_at
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
	`, doc.ID, doc.KnowledgeBaseID, doc.WorkspaceID, doc.FileID, doc.Filename, doc.DocumentRole, doc.Namespace, doc.Status, doc.CreatedBy, doc.CreatedAt, doc.UpdatedAt)
	return mapDBError(err, "knowledge document already exists", "knowledge document not found")
}

func (r *PGXKnowledgeDocumentRepo) GetByID(ctx context.Context, id uuid.UUID) (*KnowledgeDocument, error) {
	return scanKnowledgeDocument(r.pool.QueryRow(ctx, selectKnowledgeDocumentSQL()+` WHERE id = $1`, id))
}

func (r *PGXKnowledgeDocumentRepo) ListByKnowledgeBase(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int) ([]KnowledgeDocument, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	var rows pgx.Rows
	var err error
	if status != "" {
		rows, err = r.pool.Query(ctx, selectKnowledgeDocumentSQL()+` WHERE knowledge_base_id = $1 AND status = $2 ORDER BY created_at DESC LIMIT $3 OFFSET $4`, kbID, status, limit, offset)
	} else {
		rows, err = r.pool.Query(ctx, selectKnowledgeDocumentSQL()+` WHERE knowledge_base_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3`, kbID, limit, offset)
	}
	if err != nil {
		return nil, mapDBError(err, "list knowledge documents conflict", "knowledge documents not found")
	}
	return scanKnowledgeDocuments(rows)
}

func (r *PGXKnowledgeDocumentRepo) ListActiveByKnowledgeBase(ctx context.Context, kbID uuid.UUID) ([]KnowledgeDocument, error) {
	rows, err := r.pool.Query(ctx, selectKnowledgeDocumentSQL()+` WHERE knowledge_base_id = $1 AND status <> $2 ORDER BY created_at ASC`, kbID, KnowledgeDocumentStatusDeleted)
	if err != nil {
		return nil, mapDBError(err, "list active knowledge documents conflict", "knowledge documents not found")
	}
	return scanKnowledgeDocuments(rows)
}

func (r *PGXKnowledgeDocumentRepo) MarkStatus(ctx context.Context, id uuid.UUID, status string, errMsg string) error {
	_ = errMsg
	tag, err := r.pool.Exec(ctx, `
		UPDATE knowledge_documents SET status = $2, updated_at = now() WHERE id = $1
	`, id, status)
	if err != nil {
		return mapDBError(err, "update knowledge document status conflict", "knowledge document not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge document not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXKnowledgeDocumentRepo) SoftDelete(ctx context.Context, id uuid.UUID) error {
	tag, err := r.pool.Exec(ctx, `
		UPDATE knowledge_documents
		SET status = $2, deleted_at = COALESCE(deleted_at, now()), updated_at = now()
		WHERE id = $1
	`, id, KnowledgeDocumentStatusDeleted)
	if err != nil {
		return mapDBError(err, "delete knowledge document conflict", "knowledge document not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge document not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func selectKnowledgeDocumentSQL() string {
	return `
		SELECT id, knowledge_base_id, workspace_id, file_id, filename, document_role, namespace,
			status, created_by, created_at, updated_at, deleted_at, last_ingested_at
		FROM knowledge_documents`
}

func scanKnowledgeDocuments(rows pgx.Rows) ([]KnowledgeDocument, error) {
	defer rows.Close()
	var out []KnowledgeDocument
	for rows.Next() {
		item, err := scanKnowledgeDocument(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *item)
	}
	return out, mapDBError(rows.Err(), "scan knowledge documents conflict", "knowledge documents not found")
}

func scanKnowledgeDocument(row pgx.Row) (*KnowledgeDocument, error) {
	var doc KnowledgeDocument
	err := row.Scan(&doc.ID, &doc.KnowledgeBaseID, &doc.WorkspaceID, &doc.FileID, &doc.Filename, &doc.DocumentRole, &doc.Namespace,
		&doc.Status, &doc.CreatedBy, &doc.CreatedAt, &doc.UpdatedAt, &doc.DeletedAt, &doc.LastIngestedAt)
	if err != nil {
		return nil, mapDBError(err, "knowledge document conflict", "knowledge document not found")
	}
	return &doc, nil
}

type PGXKnowledgeIndexVersionRepo struct {
	pool *pgxpool.Pool
}

func NewPGXKnowledgeIndexVersionRepo(pool *pgxpool.Pool) *PGXKnowledgeIndexVersionRepo {
	return &PGXKnowledgeIndexVersionRepo{pool: pool}
}

func (r *PGXKnowledgeIndexVersionRepo) Create(ctx context.Context, version KnowledgeIndexVersion) error {
	if version.CreatedAt.IsZero() {
		version.CreatedAt = time.Now().UTC()
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO knowledge_index_versions (
			id, knowledge_base_id, workspace_id, version, qdrant_collection, qdrant_namespace,
			artifact_dir, manifest_path, status, document_count, chunk_count, created_by,
			created_at, ready_at, failed_at, error_message
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
	`, version.ID, version.KnowledgeBaseID, version.WorkspaceID, version.Version, version.QdrantCollection, version.QdrantNamespace,
		version.ArtifactDir, version.ManifestPath, version.Status, version.DocumentCount, version.ChunkCount, version.CreatedBy,
		version.CreatedAt, version.ReadyAt, version.FailedAt, version.ErrorMessage)
	return mapDBError(err, "knowledge index version already exists", "knowledge index version not found")
}

func (r *PGXKnowledgeIndexVersionRepo) GetByID(ctx context.Context, id uuid.UUID) (*KnowledgeIndexVersion, error) {
	return scanKnowledgeIndexVersion(r.pool.QueryRow(ctx, selectKnowledgeIndexVersionSQL()+` WHERE id = $1`, id))
}

func (r *PGXKnowledgeIndexVersionRepo) ListByKnowledgeBase(ctx context.Context, kbID uuid.UUID, limit int, offset int) ([]KnowledgeIndexVersion, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := r.pool.Query(ctx, selectKnowledgeIndexVersionSQL()+` WHERE knowledge_base_id = $1 ORDER BY version DESC LIMIT $2 OFFSET $3`, kbID, limit, offset)
	if err != nil {
		return nil, mapDBError(err, "list knowledge index versions conflict", "knowledge index versions not found")
	}
	defer rows.Close()
	var out []KnowledgeIndexVersion
	for rows.Next() {
		item, err := scanKnowledgeIndexVersion(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *item)
	}
	return out, mapDBError(rows.Err(), "list knowledge index versions conflict", "knowledge index versions not found")
}

func (r *PGXKnowledgeIndexVersionRepo) NextVersion(ctx context.Context, kbID uuid.UUID) (int, error) {
	var next int
	err := r.pool.QueryRow(ctx, `SELECT COALESCE(MAX(version), 0) + 1 FROM knowledge_index_versions WHERE knowledge_base_id = $1`, kbID).Scan(&next)
	if err != nil {
		return 0, mapDBError(err, "next knowledge index version conflict", "knowledge base not found")
	}
	return next, nil
}

func (r *PGXKnowledgeIndexVersionRepo) MarkReady(ctx context.Context, id uuid.UUID, artifactDir string, manifestPath string, documentCount int, chunkCount int, readyAt time.Time) error {
	tag, err := r.pool.Exec(ctx, `
		UPDATE knowledge_index_versions
		SET status = $2, artifact_dir = $3, manifest_path = $4, document_count = $5,
			chunk_count = $6, ready_at = $7, failed_at = NULL, error_message = ''
		WHERE id = $1
	`, id, IndexVersionStatusReady, artifactDir, manifestPath, documentCount, chunkCount, readyAt)
	if err != nil {
		return mapDBError(err, "mark index version ready conflict", "knowledge index version not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge index version not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXKnowledgeIndexVersionRepo) MarkFailed(ctx context.Context, id uuid.UUID, errMsg string, failedAt time.Time) error {
	tag, err := r.pool.Exec(ctx, `
		UPDATE knowledge_index_versions
		SET status = $2, failed_at = $3, error_message = $4
		WHERE id = $1
	`, id, IndexVersionStatusFailed, failedAt, errMsg)
	if err != nil {
		return mapDBError(err, "mark index version failed conflict", "knowledge index version not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "knowledge index version not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXKnowledgeIndexVersionRepo) ArchiveOldVersions(ctx context.Context, kbID uuid.UUID, exceptID uuid.UUID) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE knowledge_index_versions
		SET status = $3
		WHERE knowledge_base_id = $1 AND id <> $2 AND status = $4
	`, kbID, exceptID, IndexVersionStatusArchived, IndexVersionStatusReady)
	return mapDBError(err, "archive old index versions conflict", "knowledge index versions not found")
}

func selectKnowledgeIndexVersionSQL() string {
	return `
		SELECT id, knowledge_base_id, workspace_id, version, qdrant_collection, COALESCE(qdrant_namespace, ''),
			COALESCE(artifact_dir, ''), COALESCE(manifest_path, ''), status, document_count, chunk_count,
			created_by, created_at, ready_at, failed_at, COALESCE(error_message, '')
		FROM knowledge_index_versions`
}

func scanKnowledgeIndexVersion(row pgx.Row) (*KnowledgeIndexVersion, error) {
	var version KnowledgeIndexVersion
	err := row.Scan(&version.ID, &version.KnowledgeBaseID, &version.WorkspaceID, &version.Version, &version.QdrantCollection, &version.QdrantNamespace,
		&version.ArtifactDir, &version.ManifestPath, &version.Status, &version.DocumentCount, &version.ChunkCount,
		&version.CreatedBy, &version.CreatedAt, &version.ReadyAt, &version.FailedAt, &version.ErrorMessage)
	if err != nil {
		return nil, mapDBError(err, "knowledge index version conflict", "knowledge index version not found")
	}
	return &version, nil
}

type PGXIngestionJobRepo struct {
	pool *pgxpool.Pool
}

func NewPGXIngestionJobRepo(pool *pgxpool.Pool) *PGXIngestionJobRepo {
	return &PGXIngestionJobRepo{pool: pool}
}

func (r *PGXIngestionJobRepo) Create(ctx context.Context, job IngestionJob) error {
	now := time.Now().UTC()
	if job.CreatedAt.IsZero() {
		job.CreatedAt = now
	}
	if job.UpdatedAt.IsZero() {
		job.UpdatedAt = now
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO ingestion_jobs (
			id, workspace_id, knowledge_base_id, index_version_id, job_id, status, progress,
			document_count, error_message, python_command, out_dir, started_at, finished_at,
			created_by, created_at, updated_at
		)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
	`, job.ID, job.WorkspaceID, job.KnowledgeBaseID, job.IndexVersionID, job.JobID, job.Status, job.Progress,
		job.DocumentCount, job.ErrorMessage, job.PythonCommand, job.OutDir, job.StartedAt, job.FinishedAt,
		job.CreatedBy, job.CreatedAt, job.UpdatedAt)
	return mapDBError(err, "ingestion job already exists", "ingestion job not found")
}

func (r *PGXIngestionJobRepo) GetByID(ctx context.Context, id uuid.UUID) (*IngestionJob, error) {
	return scanIngestionJob(r.pool.QueryRow(ctx, selectIngestionJobSQL()+` WHERE id = $1`, id))
}

func (r *PGXIngestionJobRepo) ListByKnowledgeBase(ctx context.Context, kbID uuid.UUID, status string, limit int, offset int) ([]IngestionJob, error) {
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	var rows pgx.Rows
	var err error
	if status != "" {
		rows, err = r.pool.Query(ctx, selectIngestionJobSQL()+` WHERE knowledge_base_id = $1 AND status = $2 ORDER BY created_at DESC LIMIT $3 OFFSET $4`, kbID, status, limit, offset)
	} else {
		rows, err = r.pool.Query(ctx, selectIngestionJobSQL()+` WHERE knowledge_base_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3`, kbID, limit, offset)
	}
	if err != nil {
		return nil, mapDBError(err, "list ingestion jobs conflict", "ingestion jobs not found")
	}
	defer rows.Close()
	var out []IngestionJob
	for rows.Next() {
		item, err := scanIngestionJob(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, *item)
	}
	return out, mapDBError(rows.Err(), "list ingestion jobs conflict", "ingestion jobs not found")
}

func (r *PGXIngestionJobRepo) AttachJob(ctx context.Context, ingestionJobID uuid.UUID, jobID uuid.UUID, queuedAt time.Time) error {
	return r.updateStatus(ctx, ingestionJobID, []string{IngestionJobStatusCreated}, IngestionJobStatusQueued, `
		job_id = $4, updated_at = now()
	`, jobID)
}

func (r *PGXIngestionJobRepo) MarkRunning(ctx context.Context, id uuid.UUID, startedAt time.Time) error {
	return r.updateStatus(ctx, id, []string{IngestionJobStatusCreated, IngestionJobStatusQueued}, IngestionJobStatusRunning, `
		started_at = COALESCE(started_at, $4), updated_at = now()
	`, startedAt)
}

func (r *PGXIngestionJobRepo) MarkSucceeded(ctx context.Context, id uuid.UUID, finishedAt time.Time, progress int) error {
	return r.updateStatus(ctx, id, []string{IngestionJobStatusRunning, IngestionJobStatusQueued}, IngestionJobStatusSucceeded, `
		progress = $4, finished_at = $5, error_message = '', updated_at = now()
	`, progress, finishedAt)
}

func (r *PGXIngestionJobRepo) MarkFailed(ctx context.Context, id uuid.UUID, finishedAt time.Time, errMsg string) error {
	return r.updateStatus(ctx, id, []string{IngestionJobStatusCreated, IngestionJobStatusQueued, IngestionJobStatusRunning, IngestionJobStatusCancelRequested}, IngestionJobStatusFailed, `
		finished_at = $4, error_message = $5, updated_at = now()
	`, finishedAt, errMsg)
}

func (r *PGXIngestionJobRepo) RequestCancel(ctx context.Context, id uuid.UUID, t time.Time) error {
	_ = t
	return r.updateStatus(ctx, id, []string{IngestionJobStatusRunning}, IngestionJobStatusCancelRequested, `updated_at = now()`)
}

func (r *PGXIngestionJobRepo) MarkCanceled(ctx context.Context, id uuid.UUID, finishedAt time.Time) error {
	return r.updateStatus(ctx, id, []string{IngestionJobStatusCreated, IngestionJobStatusQueued, IngestionJobStatusRunning, IngestionJobStatusCancelRequested}, IngestionJobStatusCanceled, `
		finished_at = $4, updated_at = now()
	`, finishedAt)
}

func (r *PGXIngestionJobRepo) UpdateProgress(ctx context.Context, id uuid.UUID, progress int) error {
	tag, err := r.pool.Exec(ctx, `UPDATE ingestion_jobs SET progress = $2, updated_at = now() WHERE id = $1`, id, progress)
	if err != nil {
		return mapDBError(err, "update ingestion progress conflict", "ingestion job not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeNotFound, "ingestion job not found", http.StatusNotFound, nil, nil)
	}
	return nil
}

func (r *PGXIngestionJobRepo) updateStatus(ctx context.Context, id uuid.UUID, from []string, to string, setClause string, args ...any) error {
	queryArgs := []any{id, from, to}
	queryArgs = append(queryArgs, args...)
	tag, err := r.pool.Exec(ctx, `UPDATE ingestion_jobs SET status = $3, `+setClause+` WHERE id = $1 AND status = ANY($2)`, queryArgs...)
	if err != nil {
		return mapDBError(err, "update ingestion job conflict", "ingestion job not found")
	}
	if tag.RowsAffected() == 0 {
		return httpx.NewAppError(httpx.CodeConflict, "ingestion job status changed", http.StatusConflict, nil, nil)
	}
	return nil
}

func selectIngestionJobSQL() string {
	return `
		SELECT id, workspace_id, knowledge_base_id, index_version_id, job_id, status, progress,
			document_count, COALESCE(error_message, ''), COALESCE(python_command, ''), COALESCE(out_dir, ''),
			started_at, finished_at, created_by, created_at, updated_at
		FROM ingestion_jobs`
}

func scanIngestionJob(row pgx.Row) (*IngestionJob, error) {
	var job IngestionJob
	err := row.Scan(&job.ID, &job.WorkspaceID, &job.KnowledgeBaseID, &job.IndexVersionID, &job.JobID, &job.Status, &job.Progress,
		&job.DocumentCount, &job.ErrorMessage, &job.PythonCommand, &job.OutDir, &job.StartedAt, &job.FinishedAt,
		&job.CreatedBy, &job.CreatedAt, &job.UpdatedAt)
	if err != nil {
		return nil, mapDBError(err, "ingestion job conflict", "ingestion job not found")
	}
	return &job, nil
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
