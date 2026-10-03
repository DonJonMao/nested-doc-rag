package form

import (
	"context"
	"encoding/hex"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/database"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type DeferredJobCreator interface {
	CreateJobTx(context.Context, pgx.Tx, jobs.CreateJobRequest, auth.Principal) (*jobs.Job, error)
}

type PinnedFillRunStore interface {
	CreateFillRunPinned(context.Context, FillRun, CreateFillRunRequest, auth.Principal) (*FillRun, *jobs.Job, error)
}

type PGXPinnedFillRunStore struct {
	tx   *database.TxManager
	jobs DeferredJobCreator
	cfg  config.Config
}

func NewPGXPinnedFillRunStore(pool *pgxpool.Pool, jobCreator DeferredJobCreator, cfg config.Config) *PGXPinnedFillRunStore {
	return &PGXPinnedFillRunStore{tx: database.NewTxManager(pool), jobs: jobCreator, cfg: cfg}
}

// CreateFillRunPinned freezes both current scopes, the template object, and the
// dispatch payload in one PostgreSQL transaction. Redis is used after commit.
func (s *PGXPinnedFillRunStore) CreateFillRunPinned(ctx context.Context, run FillRun, req CreateFillRunRequest, actor auth.Principal) (*FillRun, *jobs.Job, error) {
	if s.jobs == nil || actor.UserID == uuid.Nil || run.ID == uuid.Nil || run.WorkspaceID == uuid.Nil {
		return nil, nil, httpx.NewAppError(httpx.CodeInternal, "pinned fill store is not configured", http.StatusInternalServerError, nil, nil)
	}
	if run.CreatedBy != actor.UserID || run.FormFileID != req.FormFileID {
		return nil, nil, pinArgument("fill run ownership does not match request")
	}
	var job *jobs.Job
	err := s.tx.WithTx(ctx, func(ctx context.Context, tx pgx.Tx) error {
		if !auth.IsAdminRoles(actor.Roles) && (req.KnowledgeBaseID == nil || *req.KnowledgeBaseID == uuid.Nil) {
			return pinArgument("knowledge_base_id is required for non-admin fill runs")
		}
		targetID, err := resolvePinnedKnowledgeBaseID(ctx, tx, run.WorkspaceID, req.KnowledgeBaseID, strings.TrimSpace(req.TargetNamespace))
		if err != nil {
			return err
		}
		globalNamespace := strings.TrimSpace(req.GlobalNamespace)
		if globalNamespace == "" && (req.GlobalKnowledgeBaseID == nil || *req.GlobalKnowledgeBaseID == uuid.Nil) {
			globalNamespace = "global"
		}
		globalID, err := resolvePinnedKnowledgeBaseID(ctx, tx, run.WorkspaceID, req.GlobalKnowledgeBaseID, globalNamespace)
		if err != nil {
			return err
		}
		if targetID == globalID {
			return pinArgument("global knowledge base must differ from target knowledge base")
		}
		ids := []uuid.UUID{targetID, globalID}
		sort.Slice(ids, func(i, j int) bool { return ids[i].String() < ids[j].String() })
		for _, id := range ids {
			var workspaceID uuid.UUID
			if err := tx.QueryRow(ctx, `SELECT workspace_id FROM knowledge_bases WHERE id=$1 FOR UPDATE`, id).Scan(&workspaceID); err != nil {
				return mapDBError(err, "knowledge base lock conflict", "knowledge base not found")
			}
			if workspaceID != run.WorkspaceID {
				return httpx.NewAppError(httpx.CodeForbidden, "knowledge base workspace mismatch", http.StatusForbidden, nil, nil)
			}
		}
		target, targetRevision, err := knowledge.ResolveCurrentScopeTx(ctx, tx, targetID, run.WorkspaceID)
		if err != nil {
			return err
		}
		global, globalRevision, err := knowledge.ResolveCurrentScopeTx(ctx, tx, globalID, run.WorkspaceID)
		if err != nil {
			return err
		}
		if isGlobalNamespace(target.Namespace) {
			return pinArgument("global knowledge base cannot be used as target namespace")
		}
		if strings.EqualFold(target.Namespace, global.Namespace) {
			return pinArgument("global namespace must differ from target namespace")
		}
		if target.Collection != global.Collection {
			return pinConflict("target and global knowledge bases use different collections")
		}
		if requested := strings.TrimSpace(req.TargetNamespace); requested != "" && requested != target.Namespace {
			return pinConflict("target namespace does not match knowledge base")
		}
		if globalNamespace != "" && globalNamespace != global.Namespace {
			return pinConflict("global namespace does not match knowledge base")
		}
		if req.IndexVersionID != nil && *req.IndexVersionID != target.IndexVersionID {
			return pinConflict("index version is not current for knowledge base")
		}

		formFile, err := scanFormFile(tx.QueryRow(ctx, `SELECT id,workspace_id,file_id,filename,created_by,created_at FROM form_files WHERE id=$1 FOR SHARE`, run.FormFileID))
		if err != nil {
			return err
		}
		if formFile.CreatedBy != actor.UserID {
			return fillRunNotFound()
		}
		if formFile.WorkspaceID != run.WorkspaceID {
			return httpx.NewAppError(httpx.CodeForbidden, "form file workspace mismatch", http.StatusForbidden, nil, nil)
		}
		var template TemplatePin
		var status, category string
		if err := tx.QueryRow(ctx, `SELECT workspace_id,id,object_key,sha256,file_size,filename,status,file_category FROM files WHERE id=$1 FOR UPDATE`, formFile.FileID).Scan(
			&template.WorkspaceID, &template.FileID, &template.ObjectKey, &template.SHA256, &template.FileSize, &template.Filename, &status, &category,
		); err != nil {
			return mapDBError(err, "template file lock conflict", "template file not found")
		}
		if template.WorkspaceID != run.WorkspaceID || status != filepkg.FileStatusActive || category != filepkg.FileCategoryFormTemplate {
			return pinConflict("form template is not active in workspace")
		}
		template.SHA256 = strings.TrimPrefix(strings.ToLower(strings.TrimSpace(template.SHA256)), "sha256:")
		hash, err := hex.DecodeString(template.SHA256)
		if err != nil || len(hash) != 32 || strings.TrimSpace(template.ObjectKey) == "" || template.FileSize <= 0 || strings.TrimSpace(template.Filename) == "" {
			return pinConflict("form template has invalid immutable object metadata")
		}
		run.KnowledgeBaseID, run.IndexVersionID = &target.KnowledgeBaseID, &target.IndexVersionID
		run.TargetScope, run.GlobalScope = &target, &global
		run.TargetActivationRevision, run.GlobalActivationRevision = &targetRevision, &globalRevision
		run.TargetNamespace, run.GlobalNamespace = target.Namespace, global.Namespace
		run.TemplatePin = &template
		if err := createFillRun(ctx, tx, run); err != nil {
			return err
		}
		job, err = s.jobs.CreateJobTx(ctx, tx, jobs.CreateJobRequest{
			WorkspaceID: run.WorkspaceID, JobType: jobs.JobTypeFillForm, ResourceType: jobs.ResourceTypeFillRun,
			ResourceID: run.ID, Payload: BuildFillFormJobPayload(run, *formFile, s.cfg), MaxAttempts: s.cfg.Jobs.MaxAttempts,
		}, actor)
		if err != nil {
			return err
		}
		now := time.Now().UTC()
		if _, err := tx.Exec(ctx, `UPDATE fill_runs SET job_id=$2,status=$3,queued_at=$4,updated_at=$4 WHERE id=$1`, run.ID, job.ID, FillRunStatusQueued, now); err != nil {
			return mapDBError(err, "attach fill job conflict", "fill run not found")
		}
		for _, item := range []struct {
			role     string
			scope    knowledge.IndexScope
			revision int64
		}{{"target", target, targetRevision}, {"global", global, globalRevision}} {
			if _, err := tx.Exec(ctx, `INSERT INTO fill_run_index_pins(run_id,workspace_id,role,knowledge_base_id,index_version_id,activation_revision) VALUES($1,$2,$3,$4,$5,$6)`, run.ID, run.WorkspaceID, item.role, item.scope.KnowledgeBaseID, item.scope.IndexVersionID, item.revision); err != nil {
				return mapDBError(err, "index pin conflict", "index pin owner not found")
			}
		}
		if _, err := tx.Exec(ctx, `INSERT INTO fill_run_template_pins(run_id,workspace_id,file_id,object_key,sha256,file_size,filename) VALUES($1,$2,$3,$4,$5,$6,$7)`, run.ID, template.WorkspaceID, template.FileID, template.ObjectKey, template.SHA256, template.FileSize, template.Filename); err != nil {
			return mapDBError(err, "template pin conflict", "template pin owner not found")
		}
		run.JobID, run.Status, run.QueuedAt, run.UpdatedAt = &job.ID, FillRunStatusQueued, &now, now
		return nil
	})
	if err != nil {
		return nil, nil, err
	}
	return &run, job, nil
}

func resolvePinnedKnowledgeBaseID(ctx context.Context, tx pgx.Tx, workspaceID uuid.UUID, id *uuid.UUID, namespace string) (uuid.UUID, error) {
	if id != nil && *id != uuid.Nil {
		return *id, nil
	}
	if namespace == "" {
		return uuid.Nil, pinArgument("knowledge_base_id or namespace is required")
	}
	var resolved uuid.UUID
	err := tx.QueryRow(ctx, `SELECT id FROM knowledge_bases WHERE workspace_id=$1 AND namespace=$2`, workspaceID, namespace).Scan(&resolved)
	return resolved, mapDBError(err, "knowledge base namespace conflict", "knowledge base not found")
}

func pinArgument(message string) error {
	return httpx.NewAppError(httpx.CodeInvalidArgument, message, http.StatusBadRequest, nil, nil)
}
func pinConflict(message string) error {
	return httpx.NewAppError(httpx.CodeConflict, message, http.StatusConflict, nil, nil)
}
