package tests

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/database"
	filepkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/file"
	formpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/form"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/httpx"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/knowledge"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/storage"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/stretchr/testify/require"
	"go.uber.org/zap"
)

// These tests use real PostgreSQL and local object storage. Ready versions are
// explicit metadata fixtures: this suite does not claim to validate Qdrant.
func TestPhase4BPinnedFillPostgres(t *testing.T) {
	pool := newPinnedPostgres(t)
	ctx := context.Background()
	t.Run("committed dual scopes and template before dispatch", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		f.queue.check = func(job jobs.Job) error {
			var indexPins, templates int
			if err := pool.QueryRow(ctx, `SELECT count(*) FROM fill_run_index_pins WHERE run_id=$1`, job.ResourceID).Scan(&indexPins); err != nil {
				return err
			}
			if err := pool.QueryRow(ctx, `SELECT count(*) FROM fill_run_template_pins WHERE run_id=$1`, job.ResourceID).Scan(&templates); err != nil {
				return err
			}
			if indexPins != 2 || templates != 1 {
				return fmt.Errorf("dispatch before committed pins: %d/%d", indexPins, templates)
			}
			return nil
		}
		run, err := f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.NoError(t, err)
		require.Len(t, f.queue.sent, 1)
		require.Equal(t, formpkg.FillRunStatusQueued, run.Status)
		require.Equal(t, f.targetVersion, run.TargetScope.IndexVersionID)
		require.Equal(t, f.globalVersion, run.GlobalScope.IndexVersionID)
		require.Equal(t, f.template.ID, run.TemplatePin.FileID)
		require.Equal(t, f.template.ObjectKey, run.TemplatePin.ObjectKey)
		job, err := jobs.NewPGXRepo(pool).GetByID(ctx, *run.JobID)
		require.NoError(t, err)
		payload, err := formpkg.ParseFillFormJobPayload(job.Payload)
		require.NoError(t, err)
		require.Equal(t, run.TargetScope, payload.TargetScope)
		require.Equal(t, run.GlobalScope, payload.GlobalScope)
		var scopes []knowledge.IndexScope
		require.NoError(t, json.Unmarshal([]byte(payload.IndexScopesJSON), &scopes))
		require.Equal(t, []knowledge.IndexScope{*run.TargetScope, *run.GlobalScope}, scopes)
		canonical, err := knowledge.CanonicalSnapshotBytes(scopes)
		require.NoError(t, err)
		require.Equal(t, string(canonical), payload.IndexScopesJSON)
	})
	t.Run("queued run keeps both old versions after pointer changes", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		run, err := f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.NoError(t, err)
		v2 := addPinnedVersion(t, pool, f.target, f.workspace, "target", 2, "ready", knowledge.StorageVersioned, "validated")
		g2 := addPinnedVersion(t, pool, f.global, f.workspace, "global", 2, "ready", knowledge.StorageVersioned, "validated")
		_, err = pool.Exec(ctx, `UPDATE knowledge_bases SET current_index_version_id=$2,activation_revision=activation_revision+1 WHERE id=$1`, f.target, v2)
		require.NoError(t, err)
		_, err = pool.Exec(ctx, `UPDATE knowledge_bases SET current_index_version_id=$2,activation_revision=activation_revision+1 WHERE id=$1`, f.global, g2)
		require.NoError(t, err)
		old, err := formpkg.NewPGXFillRunRepo(pool).GetByID(ctx, run.ID)
		require.NoError(t, err)
		require.Equal(t, f.targetVersion, old.TargetScope.IndexVersionID)
		require.Equal(t, f.globalVersion, old.GlobalScope.IndexVersionID)
		job, err := jobs.NewPGXRepo(pool).GetByID(ctx, *run.JobID)
		require.NoError(t, err)
		payload, err := formpkg.ParseFillFormJobPayload(job.Payload)
		require.NoError(t, err)
		require.Equal(t, old.TargetScope, payload.TargetScope)
		require.Equal(t, old.GlobalScope, payload.GlobalScope)
		current, err := f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.NoError(t, err)
		require.Equal(t, v2, current.TargetScope.IndexVersionID)
		require.Equal(t, g2, current.GlobalScope.IndexVersionID)
		require.Greater(t, *current.TargetActivationRevision, *old.TargetActivationRevision)
	})
	t.Run("dirty sources and failed rebuild preserve usable V1", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		addPinnedVersion(t, pool, f.target, f.workspace, "target", 2, "failed", knowledge.StorageVersioned, "pending")
		_, err := pool.Exec(ctx, `UPDATE knowledge_bases SET source_dirty=true,source_revision=source_revision+1,status='failed' WHERE id=$1`, f.target)
		require.NoError(t, err)
		options, err := knowledge.NewPGXKnowledgeBaseRepo(pool).ListReadyOptionsByWorkspace(ctx, f.workspace, 50, 0)
		require.NoError(t, err)
		require.Len(t, options, 2)
		run, err := f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.NoError(t, err)
		require.Equal(t, f.targetVersion, run.TargetScope.IndexVersionID)
	})
	t.Run("owner only including admin and cross workspace denial", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		other := auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}
		_, err := f.service.CreateSimpleFillRun(ctx, f.simple(), other)
		requireAppError(t, err, httpx.CodeNotFound, http.StatusNotFound)
		run, err := f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.NoError(t, err)
		_, err = f.service.GetFillRun(ctx, run.ID, other)
		requireAppError(t, err, httpx.CodeNotFound, http.StatusNotFound)
		foreign := newPinnedFixture(t, pool)
		req := f.simple()
		req.GlobalKnowledgeBaseID = foreign.global
		_, err = f.service.CreateSimpleFillRun(ctx, req, f.actor)
		requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
		req = f.simple()
		req.KnowledgeBaseID = foreign.target
		_, err = f.service.CreateSimpleFillRun(ctx, req, f.actor)
		requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
		req = f.simple()
		req.WorkspaceID = foreign.workspace
		_, err = f.service.CreateSimpleFillRun(ctx, req, f.actor)
		requireAppError(t, err, httpx.CodeForbidden, http.StatusForbidden)
	})
	t.Run("operator server options and current version guard", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		actor := f.actor
		actor.Roles = []string{auth.RoleOperator}
		writeback := false
		req := formpkg.CreateFillRunRequest{FormFileID: f.form.ID, KnowledgeBaseID: &f.target, GlobalKnowledgeBaseID: &f.global,
			Rows: "999", RetrievalMode: "untrusted", PromptVersion: "untrusted", Judge: true, UseJudgeCache: true, Writeback: &writeback}
		run, err := f.service.CreateFillRun(ctx, req, actor)
		require.NoError(t, err)
		require.Equal(t, f.cfg.Python.Step15DefaultRows, run.RowsSpec)
		require.Equal(t, f.cfg.Python.Step15DefaultRetrievalMode, run.RetrievalMode)
		require.Equal(t, f.cfg.Python.Step15DefaultPromptVersion, run.PromptVersion)
		require.False(t, run.JudgeEnabled)
		require.False(t, run.UseJudgeCache)
		require.True(t, run.WritebackEnabled)
		wrong := uuid.New()
		req.IndexVersionID = &wrong
		_, err = f.service.CreateFillRun(ctx, req, actor)
		requireAppError(t, err, httpx.CodeConflict, http.StatusConflict)
		req.IndexVersionID, req.KnowledgeBaseID, req.TargetNamespace = nil, nil, "target"
		_, err = f.service.CreateFillRun(ctx, req, actor)
		requireAppError(t, err, httpx.CodeInvalidArgument, http.StatusBadRequest)
	})
	t.Run("namespace and collection mismatch fail closed", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		req := formpkg.CreateFillRunRequest{WorkspaceID: f.workspace, FormFileID: f.form.ID, KnowledgeBaseID: &f.target, GlobalKnowledgeBaseID: &f.global, TargetNamespace: "wrong"}
		_, err := f.service.CreateFillRun(ctx, req, f.actor)
		requireAppError(t, err, httpx.CodeConflict, http.StatusConflict)
		req.TargetNamespace = ""
		req.GlobalNamespace = "wrong"
		_, err = f.service.CreateFillRun(ctx, req, f.actor)
		requireAppError(t, err, httpx.CodeConflict, http.StatusConflict)
		other := addPinnedVersion(t, pool, f.global, f.workspace, "global", 2, "ready", knowledge.StorageVersioned, "validated", "other_collection")
		_, err = pool.Exec(ctx, `UPDATE knowledge_bases SET qdrant_collection='other_collection',current_index_version_id=$2,activation_revision=activation_revision+1 WHERE id=$1`, f.global, other)
		require.NoError(t, err)
		req.GlobalNamespace = ""
		_, err = f.service.CreateFillRun(ctx, req, f.actor)
		requireAppError(t, err, httpx.CodeConflict, http.StatusConflict)
		require.Empty(t, f.queue.sent)
	})
	t.Run("admin namespace selection and automatic global are pinned", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		run, err := f.service.CreateFillRun(ctx, formpkg.CreateFillRunRequest{
			FormFileID: f.form.ID, TargetNamespace: "target", GlobalNamespace: "global",
		}, f.actor)
		require.NoError(t, err)
		require.Equal(t, f.targetVersion, run.TargetScope.IndexVersionID)
		require.Equal(t, f.globalVersion, run.GlobalScope.IndexVersionID)
		req := f.simple()
		req.GlobalKnowledgeBaseID = uuid.Nil
		automatic, err := f.service.CreateSimpleFillRun(ctx, req, f.actor)
		require.NoError(t, err)
		require.Equal(t, run.GlobalScope, automatic.GlobalScope)
	})
	t.Run("archived base is excluded despite an otherwise usable current", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		_, err := pool.Exec(ctx, `UPDATE knowledge_bases SET status='archived' WHERE id=$1`, f.target)
		require.NoError(t, err)
		options, err := knowledge.NewPGXKnowledgeBaseRepo(pool).ListReadyOptionsByWorkspace(ctx, f.workspace, 50, 0)
		require.NoError(t, err)
		require.Len(t, options, 1)
		require.Equal(t, f.global, options[0].ID)
		_, err = f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.Error(t, err)
		require.Empty(t, f.queue.sent)
	})
	t.Run("unvalidated seed never becomes validated and explicit legacy pin", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		_, err := pool.Exec(ctx, `UPDATE knowledge_index_versions SET validation_state='pending' WHERE id=$1`, f.targetVersion)
		require.NoError(t, err)
		_, err = f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.Error(t, err)
		f.targetVersion = addPinnedVersion(t, pool, f.target, f.workspace, "target", 2, "ready", knowledge.StorageLegacy, "legacy_declared_ready")
		_, err = pool.Exec(ctx, `UPDATE knowledge_bases SET current_index_version_id=$2,activation_revision=activation_revision+1 WHERE id=$1`, f.target, f.targetVersion)
		require.NoError(t, err)
		run, err := f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.NoError(t, err)
		require.Equal(t, knowledge.StorageLegacy, run.TargetScope.StorageContract)
		var state string
		require.NoError(t, pool.QueryRow(ctx, `SELECT validation_state FROM knowledge_index_versions WHERE id=$1`, f.targetVersion).Scan(&state))
		require.Equal(t, "legacy_declared_ready", state)
	})
	for _, table := range []string{"fill_run_index_pins", "fill_run_template_pins"} {
		t.Run("real SQL failure rolls back run job pins at "+table, func(t *testing.T) {
			f := newPinnedFixture(t, pool)
			_, err := pool.Exec(ctx, `CREATE FUNCTION vnext_fill_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected pin SQL failure'; END $$`)
			require.NoError(t, err)
			_, err = pool.Exec(ctx, `CREATE TRIGGER vnext_fill_failure BEFORE INSERT ON `+table+` FOR EACH ROW EXECUTE FUNCTION vnext_fill_failure()`)
			require.NoError(t, err)
			t.Cleanup(func() {
				_, _ = pool.Exec(ctx, `DROP TRIGGER vnext_fill_failure ON `+table+`; DROP FUNCTION vnext_fill_failure()`)
			})
			_, err = f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
			require.Error(t, err)
			for _, name := range []string{"fill_runs", "jobs", "fill_run_index_pins", "fill_run_template_pins"} {
				var count int
				require.NoError(t, pool.QueryRow(ctx, `SELECT count(*) FROM `+name+` WHERE workspace_id=$1`, f.workspace).Scan(&count))
				require.Zero(t, count, name)
			}
			require.Empty(t, f.queue.sent)
		})
	}
	t.Run("queue failure preserves pins and recoverable committed job", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		f.queue.failure = errors.New("synthetic Redis unavailable")
		run, err := f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.NoError(t, err)
		require.Equal(t, formpkg.FillRunStatusQueued, run.Status)
		job, err := jobs.NewPGXRepo(pool).GetByID(ctx, *run.JobID)
		require.NoError(t, err)
		require.Equal(t, jobs.JobStatusQueued, job.Status)
		f.queue.failure = nil
		_, err = f.jobs.RecoverPendingDispatch(ctx)
		require.NoError(t, err)
		found := false
		for _, sent := range f.queue.sent {
			if sent.ID == job.ID {
				found = true
			}
		}
		require.True(t, found)
		retained, err := formpkg.NewPGXFillRunRepo(pool).GetByID(ctx, run.ID)
		require.NoError(t, err)
		require.Equal(t, run.TargetScope, retained.TargetScope)
		require.Equal(t, run.TemplatePin, retained.TemplatePin)
	})
	t.Run("pinned template and source objects survive delete", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		_, err := f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.NoError(t, err)
		err = f.files.Delete(ctx, f.template.ID, f.actor)
		requireAppError(t, err, httpx.CodeConflict, http.StatusConflict)
		assertPinnedObjectExists(t, f.store, f.template)
		source := f.addFile(t, filepkg.FileCategoryKnowledgeDocument)
		docID := uuid.New()
		_, err = pool.Exec(ctx, `INSERT INTO knowledge_documents(id,knowledge_base_id,workspace_id,file_id,filename,document_role,namespace,created_by) VALUES($1,$2,$3,$4,$5,'knowledge_base','target',$6)`, docID, f.target, f.workspace, source.ID, source.Filename, f.actor.UserID)
		require.NoError(t, err)
		_, err = pool.Exec(ctx, `INSERT INTO knowledge_version_source_pins(index_version_id,document_id,file_id,workspace_id,knowledge_base_id,filename,relative_path,object_key,sha256,size_bytes,document_role) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'knowledge_base')`, f.targetVersion, docID, source.ID, f.workspace, f.target, source.Filename, "inputs/source.docx", source.ObjectKey, source.SHA256, source.FileSize)
		require.NoError(t, err)
		err = f.files.Delete(ctx, source.ID, f.actor)
		requireAppError(t, err, httpx.CodeConflict, http.StatusConflict)
		assertPinnedObjectExists(t, f.store, source)
		free := f.addFile(t, filepkg.FileCategoryFormTemplate)
		require.NoError(t, f.files.Delete(ctx, free.ID, f.actor))
		_, _, err = f.store.Get(ctx, free.ObjectKey)
		require.Error(t, err)
	})
	t.Run("legacy source retained after switch while run pins old version", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		f.targetVersion = addPinnedVersion(t, pool, f.target, f.workspace, "target", 2, "ready", knowledge.StorageLegacy, "legacy_declared_ready")
		_, err := pool.Exec(ctx, `UPDATE knowledge_bases SET current_index_version_id=$2,activation_revision=activation_revision+1 WHERE id=$1`, f.target, f.targetVersion)
		require.NoError(t, err)
		source := f.addFile(t, filepkg.FileCategoryKnowledgeDocument)
		_, err = pool.Exec(ctx, `INSERT INTO knowledge_documents(id,knowledge_base_id,workspace_id,file_id,filename,document_role,namespace,created_by) VALUES($1,$2,$3,$4,$5,'target','target',$6)`, uuid.New(), f.target, f.workspace, source.ID, source.Filename, f.actor.UserID)
		require.NoError(t, err)
		_, err = f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.NoError(t, err)
		v2 := addPinnedVersion(t, pool, f.target, f.workspace, "target", 3, "ready", knowledge.StorageVersioned, "validated")
		_, err = pool.Exec(ctx, `UPDATE knowledge_bases SET current_index_version_id=$2,activation_revision=activation_revision+1 WHERE id=$1`, f.target, v2)
		require.NoError(t, err)
		err = f.files.Delete(ctx, source.ID, f.actor)
		requireAppError(t, err, httpx.CodeConflict, http.StatusConflict)
		assertPinnedObjectExists(t, f.store, source)
	})
	t.Run("database rejects mutation of frozen scopes and all pin records", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		run, err := f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor)
		require.NoError(t, err)
		for _, statement := range []string{
			`UPDATE fill_runs SET target_scope_json=NULL WHERE id=$1`,
			`UPDATE fill_run_index_pins SET activation_revision=activation_revision+1 WHERE run_id=$1`,
			`UPDATE fill_run_template_pins SET object_key='changed' WHERE run_id=$1`,
		} {
			_, err := pool.Exec(ctx, statement, run.ID)
			require.ErrorContains(t, err, "immutable")
		}
		_, err = pool.Exec(ctx, `UPDATE knowledge_index_versions SET input_snapshot_hash=repeat('b',64) WHERE id=$1`, f.targetVersion)
		require.ErrorContains(t, err, "immutable")
		source := f.addFile(t, filepkg.FileCategoryKnowledgeDocument)
		addPinnedSourceDocument(t, f, source)
		created, err := knowledge.NewPGXBuildStore(pool).CreateBuild(ctx, knowledge.CreateBuildRequest{
			KnowledgeBaseID: f.target, WorkspaceID: f.workspace, ActorID: f.actor.UserID, Config: f.cfg,
		})
		require.NoError(t, err)
		_, err = pool.Exec(ctx, `UPDATE knowledge_version_source_pins SET object_key='changed' WHERE index_version_id=$1`, created.Version.ID)
		require.ErrorContains(t, err, "immutable")
	})
	t.Run("pin wins actual delete race", func(t *testing.T) { testPinnedDeleteRace(t, pool, true) })
	t.Run("delete wins actual pin race", func(t *testing.T) { testPinnedDeleteRace(t, pool, false) })
	t.Run("build source pin wins actual delete race", func(t *testing.T) { testBuildSourceDeleteRace(t, pool, true) })
	t.Run("source delete wins actual build race", func(t *testing.T) { testBuildSourceDeleteRace(t, pool, false) })
	t.Run("same filename source snapshot has distinct object paths", func(t *testing.T) {
		f := newPinnedFixture(t, pool)
		for range 2 {
			source := f.addFile(t, filepkg.FileCategoryKnowledgeDocument)
			addPinnedSourceDocument(t, f, source)
		}
		created, err := knowledge.NewPGXBuildStore(pool).CreateBuild(ctx, knowledge.CreateBuildRequest{
			KnowledgeBaseID: f.target, WorkspaceID: f.workspace, ActorID: f.actor.UserID, Config: f.cfg,
		})
		require.NoError(t, err)
		var snapshot knowledge.BuildInputSnapshot
		require.NoError(t, json.Unmarshal([]byte(created.InputSnapshotJSON), &snapshot))
		require.Len(t, snapshot.Documents, 2)
		require.Equal(t, snapshot.Documents[0].Filename, snapshot.Documents[1].Filename)
		require.NotEqual(t, snapshot.Documents[0].RelativePath, snapshot.Documents[1].RelativePath)
		require.Equal(t, created.InputSnapshotHash, knowledge.SnapshotBytesHash([]byte(created.InputSnapshotJSON)))
		for _, doc := range snapshot.Documents {
			require.Equal(t, doc.DocumentID.String()+"/"+doc.FileID.String()+"/"+doc.Filename, doc.RelativePath)
		}
	})
}

func newPinnedPostgres(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("VNEXT_TEST_POSTGRES_DSN")
	if dsn == "" {
		t.Skip("set VNEXT_TEST_POSTGRES_DSN for isolated real PostgreSQL tests")
	}
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, dsn)
	require.NoError(t, err)
	name := "vnext_fill_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	_, err = admin.Exec(ctx, `CREATE DATABASE `+pgx.Identifier{name}.Sanitize())
	require.NoError(t, err)
	cfg, err := pgxpool.ParseConfig(dsn)
	require.NoError(t, err)
	cfg.ConnConfig.Database, cfg.ConnConfig.RuntimeParams["application_name"], cfg.MaxConns = name, "vnext_fill_pins", 8
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	require.NoError(t, err)
	t.Cleanup(func() {
		pool.Close()
		_, err := admin.Exec(ctx, `DROP DATABASE `+pgx.Identifier{name}.Sanitize()+` WITH (FORCE)`)
		require.NoError(t, err)
		admin.Close()
	})
	require.NoError(t, database.ApplyMigrations(ctx, pool, "../migrations"))
	return pool
}

type pinnedQueue struct {
	sent    []jobs.Job
	check   func(jobs.Job) error
	failure error
}

func (q *pinnedQueue) Enqueue(_ context.Context, job jobs.Job) error {
	if q.check != nil {
		if err := q.check(job); err != nil {
			return err
		}
	}
	if q.failure != nil {
		return q.failure
	}
	q.sent = append(q.sent, job)
	return nil
}
func (q *pinnedQueue) Close() error { return nil }

type pinnedFixture struct {
	pool                                                    *pgxpool.Pool
	actor                                                   auth.Principal
	workspace, target, global, targetVersion, globalVersion uuid.UUID
	template                                                filepkg.File
	form                                                    formpkg.FormFile
	cfg                                                     config.Config
	service                                                 *formpkg.FillRunService
	jobs                                                    *jobs.Service
	files                                                   *filepkg.Service
	store                                                   *storage.LocalStorage
	queue                                                   *pinnedQueue
}

func newPinnedFixture(t *testing.T, pool *pgxpool.Pool) *pinnedFixture {
	t.Helper()
	ctx := context.Background()
	f := &pinnedFixture{pool: pool, actor: auth.Principal{UserID: uuid.New(), Roles: []string{auth.RoleAdmin}}, workspace: uuid.New(), target: uuid.New(), global: uuid.New(), cfg: *config.Default(), queue: &pinnedQueue{}}
	_, err := pool.Exec(ctx, `INSERT INTO users(id,username,password_hash) VALUES($1,$2,'anonymous-fixture')`, f.actor.UserID, "pin-"+f.actor.UserID.String())
	require.NoError(t, err)
	_, err = pool.Exec(ctx, `INSERT INTO workspaces(id,name,created_by) VALUES($1,'anonymous pin workspace',$2)`, f.workspace, f.actor.UserID)
	require.NoError(t, err)
	kbs := knowledge.NewPGXKnowledgeBaseRepo(pool)
	for _, item := range []struct {
		id uuid.UUID
		ns string
	}{{f.target, "target"}, {f.global, "global"}} {
		require.NoError(t, kbs.Create(ctx, knowledge.KnowledgeBase{ID: item.id, WorkspaceID: f.workspace, Name: item.ns, Namespace: item.ns, QdrantCollection: "vnext_test_collection", Status: knowledge.KnowledgeBaseStatusReady, CreatedBy: f.actor.UserID}))
	}
	f.targetVersion = addPinnedVersion(t, pool, f.target, f.workspace, "target", 1, "ready", knowledge.StorageVersioned, "validated")
	f.globalVersion = addPinnedVersion(t, pool, f.global, f.workspace, "global", 1, "ready", knowledge.StorageVersioned, "validated")
	for _, item := range []struct{ kb, version uuid.UUID }{{f.target, f.targetVersion}, {f.global, f.globalVersion}} {
		_, err = pool.Exec(ctx, `UPDATE knowledge_bases SET current_index_version_id=$2,activation_revision=1 WHERE id=$1`, item.kb, item.version)
		require.NoError(t, err)
	}
	f.store, err = storage.NewLocalStorage(t.TempDir())
	require.NoError(t, err)
	f.template = f.addFile(t, filepkg.FileCategoryFormTemplate)
	f.form = formpkg.FormFile{ID: uuid.New(), WorkspaceID: f.workspace, FileID: f.template.ID, Filename: f.template.Filename, CreatedBy: f.actor.UserID}
	formRepo, runRepo := formpkg.NewPGXFormFileRepo(pool), formpkg.NewPGXFillRunRepo(pool)
	require.NoError(t, formRepo.Create(ctx, f.form))
	f.cfg.Python.ProjectDir = t.TempDir()
	f.jobs = jobs.NewService(jobs.NewPGXRepo(pool), nil, f.queue, &fakeAuthorizer{}, nil, zap.NewNop(), 3)
	f.service = formpkg.NewFillRunService(runRepo, formRepo, f.jobs, nil, &fakeAuthorizer{writeErr: errors.New("fill create must remain owner-authorized")}, nil, zap.NewNop(), f.cfg)
	f.service.SetKnowledgeBaseReader(kbs)
	f.service.SetPinnedStore(formpkg.NewPGXPinnedFillRunStore(pool, f.jobs, f.cfg))
	f.files = filepkg.NewService(filepkg.NewPGXRepo(pool), f.store, &fakeAuthorizer{}, nil, nil, t.TempDir(), true)
	return f
}

func (f *pinnedFixture) simple() formpkg.CreateSimpleFillRunRequest {
	return formpkg.CreateSimpleFillRunRequest{WorkspaceID: f.workspace, KnowledgeBaseID: f.target, GlobalKnowledgeBaseID: f.global, FormFileID: f.form.ID}
}
func (f *pinnedFixture) addFile(t *testing.T, category string) filepkg.File {
	t.Helper()
	data := []byte("anonymous immutable test object; no business data")
	hash := sha256.Sum256(data)
	id := uuid.New()
	name := "template.xlsx"
	if category == filepkg.FileCategoryKnowledgeDocument {
		name = "source.docx"
	}
	file := filepkg.File{ID: id, WorkspaceID: f.workspace, Filename: name, OriginalFilename: name, ObjectKey: filepkg.BuildFileObjectKey(f.workspace, id, category, name), SHA256: hex.EncodeToString(hash[:]), FileSize: int64(len(data)), FileCategory: category, Status: filepkg.FileStatusActive, CreatedBy: f.actor.UserID}
	require.NoError(t, filepkg.NewPGXRepo(f.pool).Create(context.Background(), file))
	require.NoError(t, f.store.Put(context.Background(), file.ObjectKey, bytes.NewReader(data), file.FileSize, "application/octet-stream"))
	return file
}
func addPinnedVersion(t *testing.T, pool *pgxpool.Pool, kb, workspace uuid.UUID, namespace string, ordinal int, status, contract, state string, collection ...string) uuid.UUID {
	t.Helper()
	id := uuid.New()
	name := "vnext_test_collection"
	if len(collection) > 0 {
		name = collection[0]
	}
	_, err := pool.Exec(context.Background(), `INSERT INTO knowledge_index_versions(id,knowledge_base_id,workspace_id,version,qdrant_collection,qdrant_namespace,status,storage_contract,validation_state,document_count,chunk_count,input_snapshot_json,input_snapshot_canonical,input_snapshot_hash,validation_receipt_json) VALUES($1,$2,$3,$4,$9,$5,$6,$7,$8,1,1,'{}'::jsonb,'{}',repeat('a',64),'{}'::jsonb)`, id, kb, workspace, ordinal, namespace, status, contract, state, name)
	require.NoError(t, err)
	return id
}
func assertPinnedObjectExists(t *testing.T, store *storage.LocalStorage, file filepkg.File) {
	t.Helper()
	reader, _, err := store.Get(context.Background(), file.ObjectKey)
	require.NoError(t, err)
	require.NoError(t, reader.Close())
}

func testPinnedDeleteRace(t *testing.T, pool *pgxpool.Pool, pinWins bool) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	f := newPinnedFixture(t, pool)
	gate, err := pool.Begin(ctx)
	require.NoError(t, err)
	defer gate.Rollback(context.Background())
	_, err = gate.Exec(ctx, `SELECT pg_advisory_xact_lock(764221,31)`)
	require.NoError(t, err)
	table, column, event := "files", "id", "UPDATE"
	if pinWins {
		table, column, event = "fill_run_template_pins", "file_id", "INSERT"
	}
	_, err = pool.Exec(ctx, fmt.Sprintf(`CREATE FUNCTION vnext_fill_gate() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.%s='%s'::uuid THEN PERFORM pg_advisory_xact_lock(764221,31); END IF; RETURN NEW; END $$; CREATE TRIGGER vnext_fill_gate BEFORE %s ON %s FOR EACH ROW EXECUTE FUNCTION vnext_fill_gate()`, column, f.template.ID, event, table))
	require.NoError(t, err)
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(), `DROP TRIGGER vnext_fill_gate ON `+table+`; DROP FUNCTION vnext_fill_gate()`)
	})
	fillResult, deleteResult := make(chan error, 1), make(chan error, 1)
	fill := func() { _, err := f.service.CreateSimpleFillRun(ctx, f.simple(), f.actor); fillResult <- err }
	del := func() { deleteResult <- f.files.Delete(ctx, f.template.ID, f.actor) }
	if pinWins {
		go fill()
	} else {
		go del()
	}
	waitPinnedQueryBlocked(t, pool, `%`+event+` `+func() string {
		if pinWins {
			return "INTO "
		}
		return ""
	}()+table+`%`)
	if pinWins {
		go del()
		waitPinnedQueryBlocked(t, pool, "%SELECT status FROM files%FOR UPDATE%")
	} else {
		go fill()
		waitPinnedQueryBlocked(t, pool, "%SELECT workspace_id,id,object_key%FOR UPDATE%")
	}
	require.NoError(t, gate.Commit(ctx))
	fillErr, deleteErr := <-fillResult, <-deleteResult
	if pinWins {
		require.NoError(t, fillErr)
		requireAppError(t, deleteErr, httpx.CodeConflict, http.StatusConflict)
		assertPinnedObjectExists(t, f.store, f.template)
	} else {
		require.NoError(t, deleteErr)
		requireAppError(t, fillErr, httpx.CodeConflict, http.StatusConflict)
		var count int
		require.NoError(t, pool.QueryRow(ctx, `SELECT count(*) FROM fill_runs WHERE workspace_id=$1`, f.workspace).Scan(&count))
		require.Zero(t, count)
	}
}
func waitPinnedQueryBlocked(t *testing.T, pool *pgxpool.Pool, query string) {
	t.Helper()
	require.Eventually(t, func() bool {
		var found bool
		err := pool.QueryRow(context.Background(), `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query LIKE $1)`, query).Scan(&found)
		return err == nil && found
	}, 3*time.Second, 10*time.Millisecond, "real competing transaction did not block: "+query)
}

func addPinnedSourceDocument(t *testing.T, f *pinnedFixture, source filepkg.File) {
	t.Helper()
	require.NoError(t, knowledge.NewPGXKnowledgeDocumentRepo(f.pool).Create(context.Background(), knowledge.KnowledgeDocument{
		ID: uuid.New(), KnowledgeBaseID: f.target, WorkspaceID: f.workspace, FileID: source.ID,
		Filename: source.Filename, DocumentRole: knowledge.DocumentRoleKnowledgeBase, Namespace: "target", Status: "uploaded", CreatedBy: f.actor.UserID,
	}))
}

func testBuildSourceDeleteRace(t *testing.T, pool *pgxpool.Pool, buildWins bool) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	f := newPinnedFixture(t, pool)
	source := f.addFile(t, filepkg.FileCategoryKnowledgeDocument)
	addPinnedSourceDocument(t, f, source)
	gate, err := pool.Begin(ctx)
	require.NoError(t, err)
	defer gate.Rollback(context.Background())
	_, err = gate.Exec(ctx, `SELECT pg_advisory_xact_lock(764221,32)`)
	require.NoError(t, err)
	table, column, event := "files", "id", "UPDATE"
	if buildWins {
		table, column, event = "knowledge_version_source_pins", "file_id", "INSERT"
	}
	_, err = pool.Exec(ctx, fmt.Sprintf(`CREATE FUNCTION vnext_source_gate() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.%s='%s'::uuid THEN PERFORM pg_advisory_xact_lock(764221,32); END IF; RETURN NEW; END $$; CREATE TRIGGER vnext_source_gate BEFORE %s ON %s FOR EACH ROW EXECUTE FUNCTION vnext_source_gate()`, column, source.ID, event, table))
	require.NoError(t, err)
	t.Cleanup(func() {
		_, _ = pool.Exec(context.Background(), `DROP TRIGGER vnext_source_gate ON `+table+`; DROP FUNCTION vnext_source_gate()`)
	})
	buildResult, deleteResult := make(chan error, 1), make(chan error, 1)
	build := func() {
		_, err := knowledge.NewPGXBuildStore(pool).CreateBuild(ctx, knowledge.CreateBuildRequest{
			KnowledgeBaseID: f.target, WorkspaceID: f.workspace, ActorID: f.actor.UserID, Config: f.cfg,
		})
		buildResult <- err
	}
	del := func() { deleteResult <- f.files.Delete(ctx, source.ID, f.actor) }
	if buildWins {
		go build()
		waitPinnedQueryBlocked(t, pool, "%INSERT INTO knowledge_version_source_pins%")
		go del()
		waitPinnedQueryBlocked(t, pool, "%SELECT status FROM files%FOR UPDATE%")
	} else {
		go del()
		waitPinnedQueryBlocked(t, pool, "%UPDATE files%")
		go build()
		waitPinnedQueryBlocked(t, pool, "%SELECT id,workspace_id,filename,object_key%FOR UPDATE%")
	}
	require.NoError(t, gate.Commit(ctx))
	buildErr, deleteErr := <-buildResult, <-deleteResult
	if buildWins {
		require.NoError(t, buildErr)
		requireAppError(t, deleteErr, httpx.CodeConflict, http.StatusConflict)
		assertPinnedObjectExists(t, f.store, source)
		var count int
		require.NoError(t, pool.QueryRow(ctx, `SELECT count(*) FROM knowledge_version_source_pins WHERE file_id=$1`, source.ID).Scan(&count))
		require.Equal(t, 1, count)
	} else {
		require.NoError(t, deleteErr)
		requireAppError(t, buildErr, httpx.CodeConflict, http.StatusConflict)
		var count int
		require.NoError(t, pool.QueryRow(ctx, `SELECT count(*) FROM knowledge_index_versions WHERE knowledge_base_id=$1 AND status='building'`, f.target).Scan(&count))
		require.Zero(t, count)
		_, _, err := f.store.Get(ctx, source.ObjectKey)
		require.Error(t, err)
	}
}
