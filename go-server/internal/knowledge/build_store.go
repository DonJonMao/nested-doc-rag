package knowledge

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/database"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type CreateBuildRequest struct {
	KnowledgeBaseID uuid.UUID
	WorkspaceID     uuid.UUID
	ActorID         uuid.UUID
	Request         CreateIngestionRunRequest
	Config          config.Config
}

type BuildCreation struct {
	Ingestion         *IngestionJob
	Version           *KnowledgeIndexVersion
	Job               *jobs.Job
	InputSnapshotJSON string
	InputSnapshotHash string
}

type PGXBuildStore struct {
	pool *pgxpool.Pool
	tx   *database.TxManager
}

func NewPGXBuildStore(pool *pgxpool.Pool) *PGXBuildStore {
	return &PGXBuildStore{pool: pool, tx: database.NewTxManager(pool)}
}

func buildConflict(message string) error {
	return httpx.NewAppError(httpx.CodeConflict, message, http.StatusConflict, nil, nil)
}

func (s *PGXBuildStore) HasUsableCurrent(ctx context.Context, kbID, workspaceID uuid.UUID) (bool, error) {
	usable := false
	err := s.tx.WithTx(ctx, func(ctx context.Context, tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT id FROM knowledge_bases WHERE id=$1 AND workspace_id=$2 FOR SHARE`, kbID, workspaceID); err != nil {
			return err
		}
		_, _, err := ResolveCurrentScopeTx(ctx, tx, kbID, workspaceID)
		if err != nil {
			var appErr *httpx.AppError
			if errors.As(err, &appErr) && (appErr.Code == httpx.CodeNotFound || appErr.Code == httpx.CodeConflict) {
				return nil
			}
			return err
		}
		usable = true
		return nil
	})
	return usable, err
}

// ResolveCurrentScopeTx requires the caller to hold the knowledge-base row lock.
// Source edits and failed candidates do not change whether the current index serves.
func ResolveCurrentScopeTx(ctx context.Context, tx pgx.Tx, kbID, workspaceID uuid.UUID) (IndexScope, int64, error) {
	var scope IndexScope
	var revision int64
	var versionStatus, validation, baseNamespace, baseCollection, baseStatus string
	err := tx.QueryRow(ctx, `SELECT v.qdrant_collection,v.qdrant_namespace,v.knowledge_base_id,v.id,v.storage_contract,kb.activation_revision,v.status,v.validation_state,kb.namespace,kb.qdrant_collection,kb.status
 FROM knowledge_bases kb JOIN knowledge_index_versions v ON v.id=kb.current_index_version_id AND v.knowledge_base_id=kb.id AND v.workspace_id=kb.workspace_id
	 WHERE kb.id=$1 AND kb.workspace_id=$2`, kbID, workspaceID).Scan(&scope.Collection, &scope.Namespace, &scope.KnowledgeBaseID, &scope.IndexVersionID, &scope.StorageContract, &revision, &versionStatus, &validation, &baseNamespace, &baseCollection, &baseStatus)
	if err != nil {
		return scope, 0, mapDBError(err, "current knowledge scope conflict", "usable current knowledge index not found")
	}
	if scope.KnowledgeBaseID == uuid.Nil || scope.IndexVersionID == uuid.Nil || kbID == uuid.Nil || workspaceID == uuid.Nil || baseStatus == KnowledgeBaseStatusArchived || strings.TrimSpace(scope.Collection) == "" || strings.TrimSpace(scope.Namespace) == "" || scope.Namespace != baseNamespace || scope.Collection != baseCollection || versionStatus != IndexVersionStatusReady ||
		!((scope.StorageContract == StorageVersioned && validation == "validated") || (scope.StorageContract == StorageLegacy && validation == "legacy_declared_ready")) {
		return scope, 0, buildConflict("current knowledge index is not usable")
	}
	return scope, revision, nil
}

// CreateBuild commits the candidate, exact input bytes, source pins, ingestion
// and worker job together. Dispatch is the service's post-commit responsibility.
func (s *PGXBuildStore) CreateBuild(ctx context.Context, req CreateBuildRequest) (*BuildCreation, error) {
	if req.ActorID == uuid.Nil || req.KnowledgeBaseID == uuid.Nil || req.WorkspaceID == uuid.Nil {
		return nil, buildConflict("build owner is required")
	}
	var creation *BuildCreation
	err := s.tx.WithTx(ctx, func(ctx context.Context, tx pgx.Tx) error {
		kb, err := scanKnowledgeBase(tx.QueryRow(ctx, selectKnowledgeBaseSQL()+` WHERE id=$1 FOR UPDATE`, req.KnowledgeBaseID))
		if err != nil {
			return err
		}
		if kb.WorkspaceID != req.WorkspaceID {
			return buildConflict("knowledge base workspace mismatch")
		}
		if kb.Status == KnowledgeBaseStatusArchived {
			return buildConflict("archived knowledge base cannot build")
		}
		var sourceRev, activeRev int64
		if err := tx.QueryRow(ctx, `SELECT source_revision,activation_revision FROM knowledge_bases WHERE id=$1`, kb.ID).Scan(&sourceRev, &activeRev); err != nil {
			return err
		}
		namespace := defaultString(req.Request.QdrantNamespace, defaultString(req.Request.Namespace, kb.Namespace))
		if namespace == "" || namespace != kb.Namespace {
			return buildConflict("build namespace must match knowledge base")
		}
		if requested := strings.TrimSpace(req.Request.Namespace); requested != "" && requested != kb.Namespace {
			return buildConflict("build namespace must match knowledge base")
		}
		collection := defaultString(req.Request.QdrantCollection, kb.QdrantCollection)
		if collection == "" {
			return buildConflict("knowledge base collection is required")
		}
		rows, err := tx.Query(ctx, `SELECT id,file_id,filename,document_role FROM knowledge_documents WHERE knowledge_base_id=$1 AND workspace_id=$2 AND deleted_at IS NULL AND status<>'deleted' ORDER BY id`, kb.ID, kb.WorkspaceID)
		if err != nil {
			return err
		}
		var docs []BuildInputDocument
		for rows.Next() {
			var doc BuildInputDocument
			if err := rows.Scan(&doc.DocumentID, &doc.FileID, &doc.Filename, &doc.DocumentRole); err != nil {
				rows.Close()
				return err
			}
			if !ValidDocumentRole(doc.DocumentRole) || doc.DocumentID == uuid.Nil || doc.FileID == uuid.Nil {
				rows.Close()
				return buildConflict("source document identity or role is invalid")
			}
			docs = append(docs, doc)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return err
		}
		if len(docs) == 0 {
			return buildConflict("knowledge base has no active documents")
		}
		// The same file lock order is shared with template pinning and deletion.
		fileIDs := make([]uuid.UUID, 0, len(docs))
		seen := map[uuid.UUID]bool{}
		for _, doc := range docs {
			if !seen[doc.FileID] {
				fileIDs = append(fileIDs, doc.FileID)
				seen[doc.FileID] = true
			}
		}
		sort.Slice(fileIDs, func(i, j int) bool { return fileIDs[i].String() < fileIDs[j].String() })
		records := map[uuid.UUID]filepkg.File{}
		for _, id := range fileIDs {
			var f filepkg.File
			var deleted *time.Time
			err := tx.QueryRow(ctx, `SELECT id,workspace_id,filename,object_key,sha256,file_size,status,file_category,deleted_at FROM files WHERE id=$1 FOR UPDATE`, id).Scan(&f.ID, &f.WorkspaceID, &f.Filename, &f.ObjectKey, &f.SHA256, &f.FileSize, &f.Status, &f.FileCategory, &deleted)
			if err != nil {
				return err
			}
			f.SHA256 = strings.TrimPrefix(strings.ToLower(strings.TrimSpace(f.SHA256)), "sha256:")
			hash, err := hex.DecodeString(f.SHA256)
			if err != nil || len(hash) != 32 || f.WorkspaceID != kb.WorkspaceID || f.Status != filepkg.FileStatusActive || deleted != nil || f.FileCategory != filepkg.FileCategoryKnowledgeDocument || f.FileSize <= 0 || strings.TrimSpace(f.ObjectKey) == "" || strings.TrimSpace(f.Filename) == "" {
				return buildConflict("source file has invalid immutable object metadata")
			}
			records[id] = f
		}
		for i := range docs {
			f := records[docs[i].FileID]
			docs[i].Filename = filepkg.SanitizeFilename(f.Filename)
			docs[i].ObjectKey = f.ObjectKey
			docs[i].SHA256 = f.SHA256
			docs[i].SizeBytes = f.FileSize
			docs[i].RelativePath = filepath.ToSlash(filepath.Join(docs[i].DocumentID.String(), f.ID.String(), docs[i].Filename))
		}
		var ordinal int
		if err := tx.QueryRow(ctx, `SELECT COALESCE(MAX(version),0)+1 FROM knowledge_index_versions WHERE knowledge_base_id=$1`, kb.ID).Scan(&ordinal); err != nil {
			return err
		}
		now := time.Now().UTC()
		versionID, ingestionID, jobID := uuid.New(), uuid.New(), uuid.New()
		snapshot := BuildInputSnapshot{SchemaVersion: BuildInputSchemaVersion, WorkspaceID: kb.WorkspaceID, KnowledgeBaseID: kb.ID, IndexVersionID: versionID, Collection: collection, Namespace: namespace, Documents: docs}
		canonical, err := CanonicalSnapshotBytes(snapshot)
		if err != nil {
			return err
		}
		hash := SnapshotBytesHash(canonical)
		version := &KnowledgeIndexVersion{ID: versionID, KnowledgeBaseID: kb.ID, WorkspaceID: kb.WorkspaceID, Version: ordinal, QdrantCollection: collection, QdrantNamespace: namespace, Status: IndexVersionStatusBuilding, DocumentCount: len(docs), CreatedBy: req.ActorID, CreatedAt: now, StorageContract: StorageVersioned, ValidationState: "pending", SourceRevision: sourceRev, ExpectedActiveVersionID: kb.CurrentIndexVersionID, ExpectedActivationRevision: activeRev, PublicationState: "unpublished", InputSnapshotHash: hash}
		_, err = tx.Exec(ctx, `INSERT INTO knowledge_index_versions(id,knowledge_base_id,workspace_id,version,qdrant_collection,qdrant_namespace,status,document_count,created_by,created_at,storage_contract,validation_state,input_snapshot_json,input_snapshot_canonical,input_snapshot_hash,source_revision,expected_active_version_id,expected_activation_revision)
   VALUES($1,$2,$3,$4,$5,$6,'building',$7,$8,$9,'versioned_v1','pending',$10,$11,$12,$13,$14,$15)`, versionID, kb.ID, kb.WorkspaceID, ordinal, collection, namespace, len(docs), req.ActorID, now, canonical, string(canonical), hash, sourceRev, kb.CurrentIndexVersionID, activeRev)
		if err != nil {
			return err
		}
		for _, doc := range docs {
			if _, err := tx.Exec(ctx, `INSERT INTO knowledge_version_source_pins(index_version_id,document_id,file_id,workspace_id,knowledge_base_id,filename,relative_path,object_key,sha256,size_bytes,document_role) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`, versionID, doc.DocumentID, doc.FileID, kb.WorkspaceID, kb.ID, doc.Filename, doc.RelativePath, doc.ObjectKey, doc.SHA256, doc.SizeBytes, doc.DocumentRole); err != nil {
				return err
			}
		}
		outDir := filepath.Join(req.Config.Python.ProjectDir, "artifacts", "ingestion", ingestionID.String())
		ingestion := &IngestionJob{ID: ingestionID, WorkspaceID: kb.WorkspaceID, KnowledgeBaseID: kb.ID, IndexVersionID: &versionID, JobID: &jobID, Status: IngestionJobStatusQueued, DocumentCount: len(docs), PythonCommand: "python -m nested_doc_rag.cli ingest-knowledge", OutDir: outDir, CreatedBy: req.ActorID, CreatedAt: now, UpdatedAt: now}
		payload := BuildIngestKnowledgeJobPayload(*ingestion, *kb, *version, req.Request, req.Config)
		payload["input_snapshot_json"] = string(canonical)
		payload["input_snapshot_hash"] = hash
		attempts := req.Config.Jobs.MaxAttempts
		if attempts <= 0 {
			attempts = 3
		}
		job := &jobs.Job{ID: jobID, WorkspaceID: kb.WorkspaceID, JobType: jobs.JobTypeIngestKnowledge, ResourceType: jobs.ResourceTypeKnowledgeBase, ResourceID: ingestionID, Status: jobs.JobStatusCreated, MaxAttempts: attempts, Payload: payload, CreatedBy: req.ActorID, CreatedAt: now, UpdatedAt: now}
		payloadJSON, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO jobs(id,workspace_id,job_type,resource_type,resource_id,status,max_attempts,payload_json,created_by,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$10)`, jobID, kb.WorkspaceID, job.JobType, job.ResourceType, ingestionID, job.Status, attempts, payloadJSON, req.ActorID, now); err != nil {
			return err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO ingestion_jobs(id,workspace_id,knowledge_base_id,index_version_id,job_id,status,document_count,python_command,out_dir,created_by,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11)`, ingestionID, kb.WorkspaceID, kb.ID, versionID, jobID, ingestion.Status, len(docs), ingestion.PythonCommand, outDir, req.ActorID, now); err != nil {
			return err
		}
		if _, err = tx.Exec(ctx, `UPDATE knowledge_documents SET status='indexing',updated_at=$2 WHERE id=ANY($1::uuid[]) AND deleted_at IS NULL`, documentIDs(docs), now); err != nil {
			return err
		}
		if _, err = tx.Exec(ctx, `UPDATE knowledge_bases SET status='building',updated_at=$2 WHERE id=$1`, kb.ID, now); err != nil {
			return err
		}
		creation = &BuildCreation{Ingestion: ingestion, Version: version, Job: job, InputSnapshotJSON: string(canonical), InputSnapshotHash: hash}
		return nil
	})
	return creation, err
}

func documentIDs(docs []BuildInputDocument) []uuid.UUID {
	ids := make([]uuid.UUID, len(docs))
	for i, doc := range docs {
		ids[i] = doc.DocumentID
	}
	return ids
}

type lockedBuild struct {
	ingestionID, versionID, kbID, workspaceID        uuid.UUID
	status, validation, publication, canonical, hash string
	expected                                         *uuid.UUID
	expectedRevision, sourceRevision                 int64
}

func lockBuild(ctx context.Context, tx pgx.Tx, ingestionID uuid.UUID) (lockedBuild, error) {
	var b lockedBuild
	if err := tx.QueryRow(ctx, `SELECT knowledge_base_id FROM ingestion_jobs WHERE id=$1`, ingestionID).Scan(&b.kbID); err != nil {
		return b, err
	}
	if _, err := tx.Exec(ctx, `SELECT id FROM knowledge_bases WHERE id=$1 FOR UPDATE`, b.kbID); err != nil {
		return b, err
	}
	err := tx.QueryRow(ctx, `SELECT i.id,v.id,i.knowledge_base_id,i.workspace_id,i.status,v.validation_state,v.publication_state,COALESCE(v.input_snapshot_canonical,''),COALESCE(v.input_snapshot_hash,''),v.expected_active_version_id,v.expected_activation_revision,v.source_revision
 FROM ingestion_jobs i JOIN knowledge_index_versions v ON v.id=i.index_version_id AND v.knowledge_base_id=i.knowledge_base_id AND v.workspace_id=i.workspace_id WHERE i.id=$1 FOR UPDATE OF v,i`, ingestionID).Scan(&b.ingestionID, &b.versionID, &b.kbID, &b.workspaceID, &b.status, &b.validation, &b.publication, &b.canonical, &b.hash, &b.expected, &b.expectedRevision, &b.sourceRevision)
	return b, err
}

func (s *PGXBuildStore) MarkRunning(ctx context.Context, id uuid.UUID) error {
	return s.tx.WithTx(ctx, func(ctx context.Context, tx pgx.Tx) error {
		b, err := lockBuild(ctx, tx, id)
		if err != nil {
			return err
		}
		if b.validation == "validated" {
			return nil
		}
		if b.status == IngestionJobStatusCanceled || b.status == IngestionJobStatusCancelRequested {
			return buildConflict("ingestion was canceled")
		}
		if b.canonical == "" {
			return buildConflict("legacy ingestion has no frozen build input; create a new build")
		}
		rows, err := tx.Query(ctx, `SELECT f.id,f.status,f.deleted_at,p.sha256,f.sha256,p.object_key,f.object_key,p.size_bytes,f.file_size FROM knowledge_version_source_pins p JOIN files f ON f.id=p.file_id AND f.workspace_id=p.workspace_id WHERE p.index_version_id=$1 ORDER BY f.id FOR UPDATE OF f`, b.versionID)
		if err != nil {
			return err
		}
		for rows.Next() {
			var fileID uuid.UUID
			var status, pinnedHash, currentHash, pinnedKey, currentKey string
			var deletedAt *time.Time
			var pinnedSize, currentSize int64
			if err := rows.Scan(&fileID, &status, &deletedAt, &pinnedHash, &currentHash, &pinnedKey, &currentKey, &pinnedSize, &currentSize); err != nil {
				rows.Close()
				return err
			}
			if status != "active" || deletedAt != nil || pinnedHash != strings.TrimPrefix(strings.ToLower(strings.TrimSpace(currentHash)), "sha256:") || pinnedKey != currentKey || pinnedSize != currentSize {
				rows.Close()
				return buildConflict("frozen source file is no longer available")
			}
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return err
		}
		if _, err = tx.Exec(ctx, `UPDATE knowledge_index_versions SET status='building',validation_state='pending',failed_at=NULL,error_message=NULL WHERE id=$1`, b.versionID); err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `UPDATE ingestion_jobs SET status='running',started_at=COALESCE(started_at,now()),updated_at=now() WHERE id=$1`, id)
		return err
	})
}

func (s *PGXBuildStore) CompleteBuild(ctx context.Context, id uuid.UUID, result *pythonpkg.IngestionResult) error {
	if result == nil {
		return buildConflict("index validation result is required")
	}
	return s.tx.WithTx(ctx, func(ctx context.Context, tx pgx.Tx) error {
		b, err := lockBuild(ctx, tx, id)
		if err != nil {
			return err
		}
		if b.validation == "validated" && b.status == IngestionJobStatusSucceeded {
			return nil
		}
		if b.status == IngestionJobStatusCanceled || b.status == IngestionJobStatusCancelRequested {
			return buildConflict("canceled ingestion cannot publish")
		}
		var jobStatus string
		var cancelRequestedAt *time.Time
		if err = tx.QueryRow(ctx, `SELECT j.status,j.cancel_requested_at FROM jobs j JOIN ingestion_jobs i ON i.job_id=j.id WHERE i.id=$1 FOR UPDATE OF j`, id).Scan(&jobStatus, &cancelRequestedAt); err != nil {
			return err
		}
		if jobStatus == jobs.JobStatusCanceled || jobStatus == jobs.JobStatusCancelRequested || cancelRequestedAt != nil {
			return buildConflict("canceled worker job cannot publish")
		}
		var snapshot BuildInputSnapshot
		if err = json.Unmarshal([]byte(b.canonical), &snapshot); err != nil {
			return err
		}
		if SnapshotBytesHash([]byte(b.canonical)) != b.hash {
			return buildConflict("stored input snapshot hash mismatch")
		}
		if snapshot.WorkspaceID != b.workspaceID || snapshot.KnowledgeBaseID != b.kbID || snapshot.IndexVersionID != b.versionID {
			return buildConflict("stored input snapshot owner mismatch")
		}
		var receipt ValidationReceipt
		if err = json.Unmarshal(result.ValidationReceiptJSON, &receipt); err != nil {
			return fmt.Errorf("decode index validation receipt: %w", err)
		}
		if err = receipt.Validate(snapshot, b.hash); err != nil {
			return err
		}
		if result.IngestionID != uuid.Nil && result.IngestionID != id {
			return buildConflict("ingestion validation result owner mismatch")
		}
		now := time.Now().UTC()
		if _, err = tx.Exec(ctx, `UPDATE knowledge_index_versions SET status='ready',validation_state='validated',validation_receipt_json=$2,artifact_dir=$3,manifest_path=$4,document_count=$5,chunk_count=$6,ready_at=$7,failed_at=NULL,error_message=NULL,publication_state='superseded' WHERE id=$1`, b.versionID, result.ValidationReceiptJSON, result.OutDir, result.ManifestPath, receipt.DocumentCount, receipt.ActualEvidenceCount, now); err != nil {
			return err
		}
		tag, err := tx.Exec(ctx, `UPDATE knowledge_bases SET current_index_version_id=$2,activation_revision=activation_revision+1,qdrant_collection=$3,status='ready',document_count=$4,last_ingested_at=$5,source_dirty=(source_revision<>$6),updated_at=$5 WHERE id=$1 AND current_index_version_id IS NOT DISTINCT FROM $7::uuid AND activation_revision=$8 AND status<>'archived'`, b.kbID, b.versionID, snapshot.Collection, receipt.DocumentCount, now, b.sourceRevision, b.expected, b.expectedRevision)
		if err != nil {
			return err
		}
		if tag.RowsAffected() == 1 {
			if _, err = tx.Exec(ctx, `UPDATE knowledge_index_versions SET publication_state='activated' WHERE id=$1`, b.versionID); err != nil {
				return err
			}
		}
		if _, err = tx.Exec(ctx, `UPDATE ingestion_jobs SET status='succeeded',progress=100,finished_at=$2,updated_at=$2,error_message=NULL WHERE id=$1`, id, now); err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `UPDATE knowledge_documents d SET status='indexed',last_ingested_at=$2,updated_at=$2 FROM knowledge_version_source_pins p WHERE p.index_version_id=$1 AND d.id=p.document_id AND d.deleted_at IS NULL AND d.status<>'deleted'`, b.versionID, now)
		return err
	})
}

// ReadPublishedIngestion is durable completion detection, independent of today's
// current pointer. A later rollback must never cause successful data to be rebuilt.
func (s *PGXBuildStore) ReadPublishedIngestion(ctx context.Context, id uuid.UUID) (*pythonpkg.IngestionResult, bool, error) {
	result := &pythonpkg.IngestionResult{IngestionID: id}
	var status, validation, publication string
	err := s.pool.QueryRow(ctx, `SELECT i.status,v.validation_state,v.publication_state,COALESCE(v.artifact_dir,''),COALESCE(v.manifest_path,''),v.validation_receipt_json FROM ingestion_jobs i JOIN knowledge_index_versions v ON v.id=i.index_version_id AND v.knowledge_base_id=i.knowledge_base_id AND v.workspace_id=i.workspace_id WHERE i.id=$1`, id).Scan(&status, &validation, &publication, &result.OutDir, &result.ManifestPath, &result.ValidationReceiptJSON)
	if err != nil {
		return nil, false, err
	}
	done := status == IngestionJobStatusSucceeded && validation == "validated" && (publication == "activated" || publication == "superseded")
	if !done {
		return nil, false, nil
	}
	return result, true, nil
}

func (s *PGXBuildStore) FailBuild(ctx context.Context, id uuid.UUID, message string, canceled bool) error {
	return s.tx.WithTx(ctx, func(ctx context.Context, tx pgx.Tx) error {
		b, err := lockBuild(ctx, tx, id)
		if err != nil {
			return err
		}
		return failLockedBuildTx(ctx, tx, b, message, canceled)
	})
}

func failLockedBuildTx(ctx context.Context, tx pgx.Tx, b lockedBuild, message string, canceled bool) error {
	var err error
	if b.validation == "validated" {
		return nil
	}
	if b.status == IngestionJobStatusCanceled {
		return nil
	}
	status := IngestionJobStatusFailed
	if canceled {
		status = IngestionJobStatusCanceled
	}
	// A historical ingestion can refer to the legacy serving version. Its
	// missing snapshot is a rejected old task, never permission to downgrade
	// that shared namespace or its current metadata.
	if b.canonical == "" {
		_, err = tx.Exec(ctx, `UPDATE ingestion_jobs SET status=$2,error_message=$3,finished_at=now(),updated_at=now() WHERE id=$1`, b.ingestionID, status, message)
		return err
	}
	if _, err = tx.Exec(ctx, `UPDATE knowledge_index_versions SET status='failed',validation_state='failed',error_message=$2,failed_at=now() WHERE id=$1`, b.versionID, message); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `UPDATE ingestion_jobs SET status=$2,error_message=$3,finished_at=now(),updated_at=now() WHERE id=$1`, b.ingestionID, status, message); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `UPDATE knowledge_bases SET status=CASE WHEN status='archived' THEN 'archived' WHEN EXISTS(SELECT 1 FROM knowledge_index_versions v WHERE v.id=knowledge_bases.current_index_version_id AND v.status='ready' AND v.validation_state IN ('validated','legacy_declared_ready')) THEN 'ready' ELSE 'failed' END,updated_at=now() WHERE id=$1`, b.kbID); err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `UPDATE knowledge_documents d SET status='uploaded',updated_at=now() FROM knowledge_version_source_pins p WHERE p.index_version_id=$1 AND d.id=p.document_id AND d.deleted_at IS NULL AND d.status<>'deleted'`, b.versionID)
	return err
}

// CancelBuild uses the publication lock order, so cancellation and validated
// completion have one serial decision. Caller already checked admin/workspace.
func (s *PGXBuildStore) CancelBuild(ctx context.Context, id, actorID uuid.UUID) (*IngestionJob, error) {
	return s.cancelBuild(ctx, id, actorID, nil)
}

// CancelWorkerJob is the jobs service hook without a reverse package import.
// nil,nil explicitly means the legacy job has no ingestion association.
func (s *PGXBuildStore) CancelWorkerJob(ctx context.Context, jobID, actorID uuid.UUID) (*jobs.Job, error) {
	rows, err := s.pool.Query(ctx, `SELECT id FROM ingestion_jobs WHERE job_id=$1 ORDER BY id LIMIT 2`, jobID)
	if err != nil {
		return nil, err
	}
	var ingestionID uuid.UUID
	count := 0
	for rows.Next() {
		if err = rows.Scan(&ingestionID); err != nil {
			rows.Close()
			return nil, err
		}
		count++
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	if count == 0 {
		return nil, nil
	}
	if count > 1 {
		return nil, buildConflict("worker job has ambiguous ingestion associations")
	}
	if _, err = s.cancelBuild(ctx, ingestionID, actorID, &jobID); err != nil {
		return nil, err
	}
	return jobs.NewPGXRepo(s.pool).GetByID(ctx, jobID)
}

func (s *PGXBuildStore) cancelBuild(ctx context.Context, id, actorID uuid.UUID, expectedJobID *uuid.UUID) (*IngestionJob, error) {
	var ingestion *IngestionJob
	err := s.tx.WithTx(ctx, func(ctx context.Context, tx pgx.Tx) error {
		b, err := lockBuild(ctx, tx, id)
		if err != nil {
			return err
		}
		var jobID, owner uuid.UUID
		var jobStatus string
		if err := tx.QueryRow(ctx, `SELECT j.id,j.created_by,j.status FROM jobs j JOIN ingestion_jobs i ON i.job_id=j.id WHERE i.id=$1 FOR UPDATE OF j`, id).Scan(&jobID, &owner, &jobStatus); err != nil {
			return err
		}
		if actorID == uuid.Nil || owner != actorID {
			return httpx.NewAppError(httpx.CodeNotFound, "job not found", http.StatusNotFound, nil, nil)
		}
		if expectedJobID != nil && jobID != *expectedJobID {
			return buildConflict("ingestion worker association changed; reload before canceling")
		}
		if b.validation == "validated" {
			return buildConflict("validated ingestion cannot be canceled")
		}
		switch jobStatus {
		case jobs.JobStatusCreated, jobs.JobStatusQueued:
			if _, err = tx.Exec(ctx, `UPDATE jobs SET status='canceled',finished_at=now(),updated_at=now() WHERE id=$1`, jobID); err != nil {
				return err
			}
			if err = failLockedBuildTx(ctx, tx, b, "canceled", true); err != nil {
				return err
			}
		case jobs.JobStatusRunning, jobs.JobStatusCancelRequested:
			if _, err = tx.Exec(ctx, `UPDATE jobs SET status='cancel_requested',cancel_requested_at=COALESCE(cancel_requested_at,now()),updated_at=now() WHERE id=$1`, jobID); err != nil {
				return err
			}
			if _, err = tx.Exec(ctx, `UPDATE ingestion_jobs SET status='cancel_requested',updated_at=now() WHERE id=$1`, id); err != nil {
				return err
			}
		case jobs.JobStatusCanceled:
			if err = failLockedBuildTx(ctx, tx, b, "canceled", true); err != nil {
				return err
			}
		default:
			return buildConflict("ingestion worker job cannot be canceled from current status")
		}
		ingestion, err = scanIngestionJob(tx.QueryRow(ctx, selectIngestionJobSQL()+` WHERE id=$1`, id))
		return err
	})
	return ingestion, err
}

func (s *PGXBuildStore) ActivateVersion(ctx context.Context, kbID, workspaceID, versionID uuid.UUID, expected *uuid.UUID, revision int64) error {
	return s.tx.WithTx(ctx, func(ctx context.Context, tx pgx.Tx) error {
		var current *uuid.UUID
		var actualRevision int64
		var namespace, baseStatus string
		if err := tx.QueryRow(ctx, `SELECT current_index_version_id,activation_revision,namespace,status FROM knowledge_bases WHERE id=$1 AND workspace_id=$2 FOR UPDATE`, kbID, workspaceID).Scan(&current, &actualRevision, &namespace, &baseStatus); err != nil {
			return err
		}
		if baseStatus == KnowledgeBaseStatusArchived {
			return buildConflict("archived knowledge base cannot activate")
		}
		if actualRevision != revision || !sameUUIDPointer(current, expected) {
			return buildConflict("knowledge activation changed; reload before activating")
		}
		var collection, versionNamespace, storage, validation, status string
		var sourceRev int64
		if err := tx.QueryRow(ctx, `SELECT qdrant_collection,qdrant_namespace,storage_contract,validation_state,status,source_revision FROM knowledge_index_versions WHERE id=$1 AND knowledge_base_id=$2 AND workspace_id=$3 FOR UPDATE`, versionID, kbID, workspaceID).Scan(&collection, &versionNamespace, &storage, &validation, &status, &sourceRev); err != nil {
			return err
		}
		if status != "ready" || collection == "" || versionNamespace != namespace || !((storage == StorageVersioned && validation == "validated") || (storage == StorageLegacy && validation == "legacy_declared_ready")) {
			return buildConflict("index version is not usable for activation")
		}
		if current != nil && *current == versionID {
			return nil
		}
		_, err := tx.Exec(ctx, `UPDATE knowledge_bases SET current_index_version_id=$2,activation_revision=activation_revision+1,qdrant_collection=$3,status='ready',source_dirty=(source_revision<>$4),last_ingested_at=now(),updated_at=now() WHERE id=$1`, kbID, versionID, collection, sourceRev)
		return err
	})
}

func sameUUIDPointer(a, b *uuid.UUID) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == *b
}
