package knowledge

import (
	"context"
	"encoding/json"
	"os"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/DonJonMao/nested-doc-rag/go-server/internal/auth"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/config"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/database"
	"github.com/DonJonMao/nested-doc-rag/go-server/internal/jobs"
	pythonpkg "github.com/DonJonMao/nested-doc-rag/go-server/internal/python"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"go.uber.org/zap"
)

type buildFixture struct {
	pool                                 *pgxpool.Pool
	store                                *PGXBuildStore
	kbID, workspaceID, actorID, original uuid.UUID
	request                              CreateBuildRequest
}

func newBuildFixture(t *testing.T) *buildFixture {
	t.Helper()
	dsn := os.Getenv("VNEXT_TEST_POSTGRES_DSN")
	if dsn == "" {
		t.Skip("VNEXT_TEST_POSTGRES_DSN required for real PostgreSQL publication tests")
	}
	ctx := context.Background()
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	admin, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	name := "vnext_build_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err = admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	cfg = cfg.Copy()
	cfg.ConnConfig.Database = name
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		pool.Close()
		_, err := admin.Exec(context.Background(), "DROP DATABASE "+pgx.Identifier{name}.Sanitize()+" WITH (FORCE)")
		admin.Close()
		if err != nil {
			t.Error(err)
		}
	})
	if err = database.ApplyMigrations(ctx, pool, "../../migrations"); err != nil {
		t.Fatal(err)
	}
	f := &buildFixture{pool: pool, store: NewPGXBuildStore(pool), actorID: uuid.New()}
	f.exec(t, `INSERT INTO users(id,username,password_hash) VALUES($1,$2,'PG-only fixture')`, f.actorID, f.actorID.String())
	if err = pool.QueryRow(ctx, `SELECT id,workspace_id,current_index_version_id FROM knowledge_bases WHERE namespace='xianyang'`).Scan(&f.kbID, &f.workspaceID, &f.original); err != nil {
		t.Fatal(err)
	}
	var cfgBuild config.Config
	cfgBuild.Python.ProjectDir = t.TempDir()
	cfgBuild.Jobs.MaxAttempts = 3
	f.request = CreateBuildRequest{KnowledgeBaseID: f.kbID, WorkspaceID: f.workspaceID, ActorID: f.actorID, Request: CreateIngestionRunRequest{KnowledgeBaseID: f.kbID}, Config: cfgBuild}
	return f
}

func (f *buildFixture) exec(t *testing.T, sql string, args ...any) {
	t.Helper()
	if _, err := f.pool.Exec(context.Background(), sql, args...); err != nil {
		t.Fatal(err)
	}
}
func (f *buildFixture) create(t *testing.T) *BuildCreation {
	t.Helper()
	c, err := f.store.CreateBuild(context.Background(), f.request)
	if err != nil {
		t.Fatal(err)
	}
	return c
}
func (f *buildFixture) current(t *testing.T) (uuid.UUID, int64) {
	t.Helper()
	var id uuid.UUID
	var rev int64
	if err := f.pool.QueryRow(context.Background(), `SELECT current_index_version_id,activation_revision FROM knowledge_bases WHERE id=$1`, f.kbID).Scan(&id, &rev); err != nil {
		t.Fatal(err)
	}
	return id, rev
}

// Receipts here are PG protocol fixtures: these tests never claim to verify a
// Qdrant collection or call an external model. Python validates real index data.
func pgReceipt(t *testing.T, c *BuildCreation) ValidationReceipt {
	t.Helper()
	var snapshot BuildInputSnapshot
	if err := json.Unmarshal([]byte(c.InputSnapshotJSON), &snapshot); err != nil {
		t.Fatal(err)
	}
	return ValidationReceipt{SchemaVersion: ValidationSchemaVersion, IndexVersionID: c.Version.ID, KnowledgeBaseID: c.Version.KnowledgeBaseID, Namespace: c.Version.QdrantNamespace, Collection: c.Version.QdrantCollection, InputSnapshotHash: c.InputSnapshotHash, ExpectedEvidenceCount: 5, ActualEvidenceCount: 5, ExpectedSchemaCount: 2, ActualSchemaCount: 2, DocumentCount: int64(len(snapshot.Documents)), SourceHashesVerified: true, SmokePassed: true, ValidatedAt: time.Now().UTC()}
}
func pgResult(t *testing.T, c *BuildCreation, r ValidationReceipt) *pythonpkg.IngestionResult {
	t.Helper()
	data, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	return &pythonpkg.IngestionResult{IngestionID: c.Ingestion.ID, OutDir: c.Ingestion.OutDir, ManifestPath: c.Ingestion.OutDir + "/manifest.json", ValidationReceiptJSON: data}
}
func (f *buildFixture) complete(t *testing.T, c *BuildCreation) {
	t.Helper()
	if err := f.store.CompleteBuild(context.Background(), c.Ingestion.ID, pgResult(t, c, pgReceipt(t, c))); err != nil {
		t.Fatal(err)
	}
}

func TestPostgresBuildAtomicFrozenConcurrent(t *testing.T) {
	f := newBuildFixture(t)
	ctx := context.Background()
	var wg sync.WaitGroup
	results := make(chan *BuildCreation, 6)
	errs := make(chan error, 6)
	for i := 0; i < 6; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); c, err := f.store.CreateBuild(ctx, f.request); results <- c; errs <- err }()
	}
	wg.Wait()
	close(results)
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	var ordinals []int
	for c := range results {
		ordinals = append(ordinals, c.Version.Version)
		var snapshot BuildInputSnapshot
		if err := json.Unmarshal([]byte(c.InputSnapshotJSON), &snapshot); err != nil {
			t.Fatal(err)
		}
		canonical, err := CanonicalSnapshotBytes(snapshot)
		if err != nil || string(canonical) != c.InputSnapshotJSON || SnapshotBytesHash(canonical) != c.InputSnapshotHash {
			t.Fatalf("noncanonical snapshot %v", err)
		}
		if snapshot.IndexVersionID != c.Version.ID || snapshot.KnowledgeBaseID != f.kbID || snapshot.WorkspaceID != f.workspaceID {
			t.Fatal("snapshot owner differs")
		}
		for _, doc := range snapshot.Documents {
			if doc.RelativePath != doc.DocumentID.String()+"/"+doc.FileID.String()+"/"+doc.Filename {
				t.Fatal("physical input identity lost")
			}
		}
		var frozen bool
		err = f.pool.QueryRow(ctx, `SELECT v.input_snapshot_canonical=$2 AND v.input_snapshot_hash=$3 AND (SELECT count(*) FROM knowledge_version_source_pins p WHERE p.index_version_id=v.id)=v.document_count AND i.job_id=j.id AND j.payload_json->>'input_snapshot_json'=$2 AND j.payload_json->>'input_snapshot_hash'=$3 AND j.payload_json->>'storage_contract'='versioned_v1' FROM knowledge_index_versions v JOIN ingestion_jobs i ON i.index_version_id=v.id JOIN jobs j ON j.id=i.job_id WHERE v.id=$1`, c.Version.ID, c.InputSnapshotJSON, c.InputSnapshotHash).Scan(&frozen)
		if err != nil || !frozen {
			t.Fatalf("build association not frozen %v %v", frozen, err)
		}
	}
	sort.Ints(ordinals)
	for i, n := range ordinals {
		if n != i+2 {
			t.Fatalf("duplicate/gapped ordinal %v", ordinals)
		}
	}
	id, rev := f.current(t)
	if id != f.original || rev != 0 {
		t.Fatal("building candidates moved current")
	}
	f.exec(t, `CREATE FUNCTION fail_build_job_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'job association failure fixture'; END; $$; CREATE TRIGGER fail_build_job_fixture BEFORE INSERT ON jobs FOR EACH ROW EXECUTE FUNCTION fail_build_job_fixture()`)
	if _, err := f.store.CreateBuild(ctx, f.request); err == nil {
		t.Fatal("failed job association committed")
	}
	var versions, ingestions, pins int
	if err := f.pool.QueryRow(ctx, `SELECT (SELECT count(*) FROM knowledge_index_versions WHERE knowledge_base_id=$1),(SELECT count(*) FROM ingestion_jobs WHERE knowledge_base_id=$1),(SELECT count(*) FROM knowledge_version_source_pins WHERE knowledge_base_id=$1)`, f.kbID).Scan(&versions, &ingestions, &pins); err != nil {
		t.Fatal(err)
	}
	if versions != 7 || ingestions != 6 || pins != 12 {
		t.Fatalf("creation was not atomic: %d %d %d", versions, ingestions, pins)
	}
}

func TestPostgresBuildReceiptFailClosed(t *testing.T) {
	f := newBuildFixture(t)
	c := f.create(t)
	ctx := context.Background()
	cases := map[string]func(*ValidationReceipt){
		"schema": func(r *ValidationReceipt) { r.SchemaVersion = "other" }, "version": func(r *ValidationReceipt) { r.IndexVersionID = uuid.New() }, "base": func(r *ValidationReceipt) { r.KnowledgeBaseID = uuid.New() },
		"namespace": func(r *ValidationReceipt) { r.Namespace = "other" }, "collection": func(r *ValidationReceipt) { r.Collection = "other" }, "hash": func(r *ValidationReceipt) { r.InputSnapshotHash = strings.Repeat("a", 64) },
		"missing_evidence": func(r *ValidationReceipt) { r.ActualEvidenceCount = 4 }, "extra_evidence": func(r *ValidationReceipt) { r.ActualEvidenceCount = 6 }, "zero_evidence": func(r *ValidationReceipt) { r.ExpectedEvidenceCount = 0; r.ActualEvidenceCount = 0 },
		"missing_schema": func(r *ValidationReceipt) { r.ActualSchemaCount = 1 }, "extra_schema": func(r *ValidationReceipt) { r.ActualSchemaCount = 3 }, "document_count": func(r *ValidationReceipt) { r.DocumentCount = 0 },
		"source_hash": func(r *ValidationReceipt) { r.SourceHashesVerified = false }, "smoke": func(r *ValidationReceipt) { r.SmokePassed = false }, "timestamp": func(r *ValidationReceipt) { r.ValidatedAt = time.Time{} },
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			r := pgReceipt(t, c)
			change(&r)
			if err := f.store.CompleteBuild(ctx, c.Ingestion.ID, pgResult(t, c, r)); err == nil {
				t.Fatal("invalid receipt published")
			}
			id, rev := f.current(t)
			if id != f.original || rev != 0 {
				t.Fatal("invalid receipt moved current")
			}
			var validation, status string
			if err := f.pool.QueryRow(ctx, `SELECT v.validation_state,i.status FROM knowledge_index_versions v JOIN ingestion_jobs i ON i.index_version_id=v.id WHERE v.id=$1`, c.Version.ID).Scan(&validation, &status); err != nil || validation != "pending" || status != "queued" {
				t.Fatalf("invalid receipt mutated build %s %s %v", validation, status, err)
			}
		})
	}
	f.complete(t, c)
}

func TestPostgresPublicationFaultsAreAtomic(t *testing.T) {
	f := newBuildFixture(t)
	c := f.create(t)
	ctx := context.Background()
	for _, step := range []struct{ name, table, condition string }{
		{"candidate_ready", "knowledge_index_versions", "NEW.validation_state='validated'"},
		{"current_cas", "knowledge_bases", "NEW.current_index_version_id IS DISTINCT FROM OLD.current_index_version_id"},
		{"publication_state", "knowledge_index_versions", "NEW.publication_state='activated'"},
		{"ingestion_success", "ingestion_jobs", "NEW.status='succeeded'"},
		{"document_status", "knowledge_documents", "NEW.status='indexed'"},
	} {
		t.Run(step.name, func(t *testing.T) {
			f.exec(t, `CREATE FUNCTION publication_fault_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF `+step.condition+` THEN RAISE EXCEPTION 'publication failure fixture'; END IF; RETURN NEW; END; $$; CREATE TRIGGER publication_fault_fixture BEFORE UPDATE ON `+step.table+` FOR EACH ROW EXECUTE FUNCTION publication_fault_fixture()`)
			if err := f.store.CompleteBuild(ctx, c.Ingestion.ID, pgResult(t, c, pgReceipt(t, c))); err == nil {
				t.Fatal("injected publication failure committed")
			}
			id, rev := f.current(t)
			if id != f.original || rev != 0 {
				t.Fatal("partial publication changed current")
			}
			var atomic bool
			if err := f.pool.QueryRow(ctx, `SELECT v.status='building' AND v.validation_state='pending' AND v.validation_receipt_json IS NULL AND v.publication_state='unpublished' AND i.status='queued' FROM knowledge_index_versions v JOIN ingestion_jobs i ON i.index_version_id=v.id WHERE v.id=$1`, c.Version.ID).Scan(&atomic); err != nil || !atomic {
				t.Fatalf("publication did not rollback %v %v", atomic, err)
			}
			f.exec(t, `DROP TRIGGER publication_fault_fixture ON `+step.table+`; DROP FUNCTION publication_fault_fixture()`)
		})
	}
	f.complete(t, c)
}

func TestPostgresConcurrentPublicationOnlyOneActivation(t *testing.T) {
	f := newBuildFixture(t)
	ctx := context.Background()
	var candidates []*BuildCreation
	for i := 0; i < 5; i++ {
		candidates = append(candidates, f.create(t))
	}
	var wg sync.WaitGroup
	errs := make(chan error, len(candidates))
	for _, c := range candidates {
		result := pgResult(t, c, pgReceipt(t, c))
		wg.Add(1)
		go func(c *BuildCreation, result *pythonpkg.IngestionResult) {
			defer wg.Done()
			errs <- f.store.CompleteBuild(ctx, c.Ingestion.ID, result)
		}(c, result)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	id, rev := f.current(t)
	if id == f.original || rev != 1 {
		t.Fatalf("concurrent publications activated more than once %s %d", id, rev)
	}
	var activated, superseded, succeeded int
	if err := f.pool.QueryRow(ctx, `SELECT (SELECT count(*) FROM knowledge_index_versions WHERE knowledge_base_id=$1 AND publication_state='activated'),(SELECT count(*) FROM knowledge_index_versions WHERE knowledge_base_id=$1 AND publication_state='superseded'),(SELECT count(*) FROM ingestion_jobs WHERE knowledge_base_id=$1 AND status='succeeded')`, f.kbID).Scan(&activated, &superseded, &succeeded); err != nil || activated != 1 || superseded != 4 || succeeded != 5 {
		t.Fatalf("concurrent completion lost readiness %d %d %d %v", activated, superseded, succeeded, err)
	}
}

func TestPostgresPublicationCASRollbackABAAndRecovery(t *testing.T) {
	f := newBuildFixture(t)
	ctx := context.Background()
	a, b := f.create(t), f.create(t)
	f.complete(t, a)
	f.complete(t, b)
	id, rev := f.current(t)
	if id != a.Version.ID || rev != 1 {
		t.Fatal("late candidate stole current")
	}
	var state, status string
	if err := f.pool.QueryRow(ctx, `SELECT publication_state,status FROM knowledge_index_versions WHERE id=$1`, b.Version.ID).Scan(&state, &status); err != nil || state != "superseded" || status != "ready" {
		t.Fatalf("stale candidate lost validated readiness %s %s %v", state, status, err)
	}
	if err := f.store.ActivateVersion(ctx, f.kbID, f.workspaceID, b.Version.ID, &id, rev); err != nil {
		t.Fatal(err)
	}
	id, rev = f.current(t)
	if err := f.store.ActivateVersion(ctx, f.kbID, f.workspaceID, a.Version.ID, &id, rev); err != nil {
		t.Fatal(err)
	}
	id, rev = f.current(t)
	if rev != 3 {
		t.Fatalf("rollback revision %d", rev)
	}
	f.complete(t, b)
	if err := f.store.FailBuild(ctx, b.Ingestion.ID, "post-publication worker persistence failed", false); err != nil {
		t.Fatal(err)
	}
	result, done, err := f.store.ReadPublishedIngestion(ctx, b.Ingestion.ID)
	if err != nil || !done || len(result.ValidationReceiptJSON) == 0 || result.OutDir != b.Ingestion.OutDir {
		t.Fatalf("durable publication recovery lost %v %v", done, err)
	}
	unchanged, unchangedRev := f.current(t)
	if unchanged != id || unchangedRev != rev {
		t.Fatal("duplicate completion reactivated version after rollback")
	}
	stale := f.create(t)
	if err := f.store.ActivateVersion(ctx, f.kbID, f.workspaceID, b.Version.ID, &id, rev); err != nil {
		t.Fatal(err)
	}
	newID, newRev := f.current(t)
	if err := f.store.ActivateVersion(ctx, f.kbID, f.workspaceID, a.Version.ID, &newID, newRev); err != nil {
		t.Fatal(err)
	}
	f.complete(t, stale)
	nowID, nowRev := f.current(t)
	if nowID != a.Version.ID || nowRev != 5 {
		t.Fatalf("ABA stole current %s %d", nowID, nowRev)
	}
	if err := f.store.ActivateVersion(ctx, f.kbID, f.workspaceID, b.Version.ID, &id, rev); err == nil {
		t.Fatal("stale activation revision accepted")
	}
	var ready int
	if err := f.pool.QueryRow(ctx, `SELECT count(*) FROM knowledge_index_versions WHERE knowledge_base_id=$1 AND status='ready'`, f.kbID).Scan(&ready); err != nil || ready != 4 {
		t.Fatalf("previous ready versions archived %d %v", ready, err)
	}
}

func TestPostgresCandidateFailureCancelAndSourceDirty(t *testing.T) {
	f := newBuildFixture(t)
	ctx := context.Background()
	current := f.create(t)
	f.complete(t, current)
	active, revision := f.current(t)
	failed := f.create(t)
	if err := f.store.FailBuild(ctx, failed.Ingestion.ID, "Python failed", false); err != nil {
		t.Fatal(err)
	}
	if _, done, err := f.store.ReadPublishedIngestion(ctx, failed.Ingestion.ID); err != nil || done {
		t.Fatalf("failed candidate detected as completed %v %v", done, err)
	}
	if err := f.store.MarkRunning(ctx, failed.Ingestion.ID); err != nil {
		t.Fatal(err)
	}
	canceled := f.create(t)
	f.exec(t, `UPDATE jobs SET status='cancel_requested',cancel_requested_at=now() WHERE id=$1`, canceled.Job.ID)
	if err := f.store.CompleteBuild(ctx, canceled.Ingestion.ID, pgResult(t, canceled, pgReceipt(t, canceled))); err == nil {
		t.Fatal("job cancellation race published")
	}
	if err := f.store.FailBuild(ctx, canceled.Ingestion.ID, "canceled", true); err != nil {
		t.Fatal(err)
	}
	docsRepo := NewPGXKnowledgeDocumentRepo(f.pool)
	var documentID uuid.UUID
	if err := f.pool.QueryRow(ctx, `SELECT id FROM knowledge_documents WHERE knowledge_base_id=$1 ORDER BY id LIMIT 1`, f.kbID).Scan(&documentID); err != nil {
		t.Fatal(err)
	}
	if err := docsRepo.SoftDelete(ctx, documentID); err != nil {
		t.Fatal(err)
	}
	if err := docsRepo.SoftDelete(ctx, documentID); err != nil {
		t.Fatal(err)
	}
	kb, err := NewPGXKnowledgeBaseRepo(f.pool).GetByID(ctx, f.kbID)
	if err != nil || !kb.SourceDirty || kb.SourceRevision != 1 {
		t.Fatalf("source revision not idempotent %+v %v", kb, err)
	}
	usable, err := f.store.HasUsableCurrent(ctx, f.kbID, f.workspaceID)
	if err != nil || !usable {
		t.Fatalf("source dirty disabled active index %v %v", usable, err)
	}
	options, err := NewPGXKnowledgeBaseRepo(f.pool).ListReadyOptionsByWorkspace(ctx, f.workspaceID, 100, 0)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, kb := range options {
		if kb.ID == f.kbID {
			found = true
		}
	}
	if !found {
		t.Fatal("ready options hid active index after candidate/source changes")
	}
	id, rev := f.current(t)
	if id != active || rev != revision {
		t.Fatal("candidate failure/cancel/source edit changed current")
	}
}

func TestPostgresCancelBuildPublicationOrderingAndOwner(t *testing.T) {
	f := newBuildFixture(t)
	ctx := context.Background()
	for _, status := range []string{"created", "queued", "running"} {
		t.Run("cancel_before_publish_"+status, func(t *testing.T) {
			c := f.create(t)
			f.exec(t, `UPDATE jobs SET status=$2 WHERE id=$1`, c.Job.ID, status)
			if _, err := f.store.CancelBuild(ctx, c.Ingestion.ID, uuid.New()); err == nil {
				t.Fatal("nonowner canceled worker job")
			}
			if _, err := f.store.CancelBuild(ctx, c.Ingestion.ID, f.actorID); err != nil {
				t.Fatal(err)
			}
			if err := f.store.CompleteBuild(ctx, c.Ingestion.ID, pgResult(t, c, pgReceipt(t, c))); err == nil {
				t.Fatal("cancellation-before-publication published")
			}
			var jobStatus, ingestionStatus, versionStatus string
			if err := f.pool.QueryRow(ctx, `SELECT j.status,i.status,v.status FROM ingestion_jobs i JOIN jobs j ON j.id=i.job_id JOIN knowledge_index_versions v ON v.id=i.index_version_id WHERE i.id=$1`, c.Ingestion.ID).Scan(&jobStatus, &ingestionStatus, &versionStatus); err != nil {
				t.Fatal(err)
			}
			if status == "running" {
				if jobStatus != "cancel_requested" || ingestionStatus != "cancel_requested" || versionStatus != "building" {
					t.Fatalf("running cancel lost atomic request: %s %s %s", jobStatus, ingestionStatus, versionStatus)
				}
			} else if jobStatus != "canceled" || ingestionStatus != "canceled" || versionStatus != "failed" {
				t.Fatalf("queued cancel lost atomic completion: %s %s %s", jobStatus, ingestionStatus, versionStatus)
			}
			id, rev := f.current(t)
			if id != f.original || rev != 0 {
				t.Fatal("cancellation moved current")
			}
		})
	}
	t.Run("publish_before_cancel", func(t *testing.T) {
		c := f.create(t)
		f.exec(t, `UPDATE jobs SET status='running' WHERE id=$1`, c.Job.ID)
		f.complete(t, c)
		if _, err := f.store.CancelBuild(ctx, c.Ingestion.ID, f.actorID); err == nil {
			t.Fatal("published completion accepted late cancel")
		}
		var retained bool
		if err := f.pool.QueryRow(ctx, `SELECT i.status='succeeded' AND j.status='running' AND j.cancel_requested_at IS NULL AND v.status='ready' AND v.validation_state='validated' FROM ingestion_jobs i JOIN jobs j ON j.id=i.job_id JOIN knowledge_index_versions v ON v.id=i.index_version_id WHERE i.id=$1`, c.Ingestion.ID).Scan(&retained); err != nil || !retained {
			t.Fatalf("late cancel dirtied committed publication %v %v", retained, err)
		}
	})
	t.Run("concurrent_serial_decision", func(t *testing.T) {
		for i := 0; i < 5; i++ {
			c := f.create(t)
			f.exec(t, `UPDATE jobs SET status='running' WHERE id=$1`, c.Job.ID)
			result := pgResult(t, c, pgReceipt(t, c))
			start := make(chan struct{})
			completeErr, cancelErr := make(chan error, 1), make(chan error, 1)
			go func() { <-start; completeErr <- f.store.CompleteBuild(ctx, c.Ingestion.ID, result) }()
			go func() { <-start; _, err := f.store.CancelBuild(ctx, c.Ingestion.ID, f.actorID); cancelErr <- err }()
			close(start)
			published, canceled := <-completeErr, <-cancelErr
			if (published == nil) == (canceled == nil) {
				t.Fatalf("concurrent publication/cancel had no single decision: publish=%v cancel=%v", published, canceled)
			}
			var jobStatus, ingestionStatus string
			if err := f.pool.QueryRow(ctx, `SELECT j.status,i.status FROM ingestion_jobs i JOIN jobs j ON j.id=i.job_id WHERE i.id=$1`, c.Ingestion.ID).Scan(&jobStatus, &ingestionStatus); err != nil {
				t.Fatal(err)
			}
			if published == nil {
				if jobStatus != "running" || ingestionStatus != "succeeded" {
					t.Fatal("winning publication has dirty cancellation")
				}
			} else if jobStatus != "cancel_requested" || ingestionStatus != "cancel_requested" {
				t.Fatal("winning cancellation has partial domain state")
			}
		}
	})
}

func TestPostgresGenericJobCancelPublicationOrdering(t *testing.T) {
	f := newBuildFixture(t)
	ctx := context.Background()
	jobRepo := jobs.NewPGXRepo(f.pool)
	t.Run("cancel_before_publish", func(t *testing.T) {
		c := f.create(t)
		f.exec(t, `UPDATE jobs SET status='running' WHERE id=$1`, c.Job.ID)
		if _, err := jobRepo.CancelUnpublishedJob(ctx, c.Job.ID, time.Now().UTC()); err != nil {
			t.Fatal(err)
		}
		if err := f.store.CompleteBuild(ctx, c.Ingestion.ID, pgResult(t, c, pgReceipt(t, c))); err == nil {
			t.Fatal("generic cancellation-before-publication published")
		}
		id, rev := f.current(t)
		if id != f.original || rev != 0 {
			t.Fatal("generic cancellation changed current")
		}
	})
	t.Run("publish_before_cancel", func(t *testing.T) {
		c := f.create(t)
		f.exec(t, `UPDATE jobs SET status='running' WHERE id=$1`, c.Job.ID)
		f.complete(t, c)
		if _, err := jobRepo.CancelUnpublishedJob(ctx, c.Job.ID, time.Now().UTC()); err == nil {
			t.Fatal("generic route canceled published build")
		}
		var clean bool
		if err := f.pool.QueryRow(ctx, `SELECT i.status='succeeded' AND j.status='running' AND j.cancel_requested_at IS NULL FROM ingestion_jobs i JOIN jobs j ON j.id=i.job_id WHERE i.id=$1`, c.Ingestion.ID).Scan(&clean); err != nil || !clean {
			t.Fatalf("generic late cancel dirtied publication %v %v", clean, err)
		}
	})
	t.Run("concurrent_decision", func(t *testing.T) {
		for i := 0; i < 5; i++ {
			c := f.create(t)
			f.exec(t, `UPDATE jobs SET status='running' WHERE id=$1`, c.Job.ID)
			result := pgResult(t, c, pgReceipt(t, c))
			start := make(chan struct{})
			completeErr, cancelErr := make(chan error, 1), make(chan error, 1)
			go func() { <-start; completeErr <- f.store.CompleteBuild(ctx, c.Ingestion.ID, result) }()
			go func() {
				<-start
				_, err := jobRepo.CancelUnpublishedJob(ctx, c.Job.ID, time.Now().UTC())
				cancelErr <- err
			}()
			close(start)
			published, canceled := <-completeErr, <-cancelErr
			if (published == nil) == (canceled == nil) {
				t.Fatalf("generic concurrent cancel/complete has no single outcome %v %v", published, canceled)
			}
			var jobStatus, domainStatus string
			if err := f.pool.QueryRow(ctx, `SELECT j.status,i.status FROM ingestion_jobs i JOIN jobs j ON j.id=i.job_id WHERE i.id=$1`, c.Ingestion.ID).Scan(&jobStatus, &domainStatus); err != nil {
				t.Fatal(err)
			}
			if published == nil {
				if jobStatus != "running" || domainStatus != "succeeded" {
					t.Fatal("generic concurrent late cancel dirtied success")
				}
			} else if jobStatus != "cancel_requested" || domainStatus != "queued" {
				t.Fatal("generic cancellation-before-publish failed closed incorrectly")
			}
		}
	})
}

type buildTestAuthorizer struct{}

func (*buildTestAuthorizer) CanReadWorkspace(context.Context, uuid.UUID, auth.Principal) error {
	return nil
}
func (*buildTestAuthorizer) CanWriteWorkspace(context.Context, uuid.UUID, auth.Principal) error {
	return nil
}

func TestPostgresGenericJobServiceDelegatesDomainCancel(t *testing.T) {
	f := newBuildFixture(t)
	ctx := context.Background()
	jobRepo := jobs.NewPGXRepo(f.pool)
	service := jobs.NewService(jobRepo, nil, nil, &buildTestAuthorizer{}, nil, zap.NewNop(), 3)
	service.SetIngestionJobCanceler(f.store)
	actor := auth.Principal{UserID: f.actorID, Roles: []string{auth.RoleAdmin}}
	for _, status := range []string{"queued", "running"} {
		t.Run(status+"_before_publication", func(t *testing.T) {
			c := f.create(t)
			f.exec(t, `UPDATE jobs SET status=$2 WHERE id=$1`, c.Job.ID, status)
			job, err := service.CancelJob(ctx, c.Job.ID, actor)
			if err != nil {
				t.Fatal(err)
			}
			var domainStatus, versionStatus string
			if err := f.pool.QueryRow(ctx, `SELECT i.status,v.status FROM ingestion_jobs i JOIN knowledge_index_versions v ON v.id=i.index_version_id WHERE i.id=$1`, c.Ingestion.ID).Scan(&domainStatus, &versionStatus); err != nil {
				t.Fatal(err)
			}
			if status == "queued" {
				if job.Status != "canceled" || domainStatus != "canceled" || versionStatus != "failed" {
					t.Fatal("generic queued cancel left orphan building state")
				}
			} else if job.Status != "cancel_requested" || domainStatus != "cancel_requested" || versionStatus != "building" {
				t.Fatal("generic running cancel did not commit both requests")
			}
			if err := f.store.CompleteBuild(ctx, c.Ingestion.ID, pgResult(t, c, pgReceipt(t, c))); err == nil {
				t.Fatal("generic delegated cancellation published")
			}
		})
	}
	t.Run("after_publication", func(t *testing.T) {
		c := f.create(t)
		f.exec(t, `UPDATE jobs SET status='running' WHERE id=$1`, c.Job.ID)
		f.complete(t, c)
		if _, err := service.CancelJob(ctx, c.Job.ID, actor); err == nil {
			t.Fatal("generic service canceled completed publication")
		}
		job, err := jobRepo.GetByID(ctx, c.Job.ID)
		if err != nil || job.Status != "running" || job.CancelRequestedAt != nil {
			t.Fatalf("late cancel dirtied worker state %+v %v", job, err)
		}
	})
	t.Run("legacy_without_ingestion_fallback", func(t *testing.T) {
		job := jobs.Job{ID: uuid.New(), WorkspaceID: f.workspaceID, JobType: jobs.JobTypeIngestKnowledge, ResourceType: jobs.ResourceTypeKnowledgeBase, ResourceID: f.kbID, Status: jobs.JobStatusCreated, MaxAttempts: 3, Payload: map[string]any{}, CreatedBy: f.actorID}
		if err := jobRepo.Create(ctx, job); err != nil {
			t.Fatal(err)
		}
		associated, err := f.store.CancelWorkerJob(ctx, job.ID, f.actorID)
		if err != nil || associated != nil {
			t.Fatalf("missing association did not explicitly permit fallback %+v %v", associated, err)
		}
		canceled, err := service.CancelJob(ctx, job.ID, actor)
		if err != nil || canceled.Status != "canceled" {
			t.Fatalf("legacy no-association fallback failed %+v %v", canceled, err)
		}
	})
}

func TestPostgresLegacyDeclarationAndImmutability(t *testing.T) {
	f := newBuildFixture(t)
	ctx := context.Background()
	var actualValidated int
	if err := f.pool.QueryRow(ctx, `SELECT count(*) FROM knowledge_index_versions WHERE validation_state='validated'`).Scan(&actualValidated); err != nil || actualValidated != 0 {
		t.Fatalf("legacy seed claimed validation %d %v", actualValidated, err)
	}
	c := f.create(t)
	for _, sql := range []string{`UPDATE knowledge_index_versions SET input_snapshot_hash=repeat('f',64) WHERE id=$1`, `UPDATE knowledge_index_versions SET qdrant_namespace='other' WHERE id=$1`, `UPDATE knowledge_version_source_pins SET object_key='changed' WHERE index_version_id=$1`} {
		if _, err := f.pool.Exec(ctx, sql, c.Version.ID); err == nil {
			t.Fatalf("frozen input was mutable: %s", sql)
		}
	}
	badID := uuid.New()
	f.exec(t, `INSERT INTO ingestion_jobs(id,workspace_id,knowledge_base_id,index_version_id,status) VALUES($1,$2,$3,$4,'queued')`, badID, f.workspaceID, f.kbID, f.original)
	if err := f.store.MarkRunning(ctx, badID); err == nil || !strings.Contains(err.Error(), "new build") {
		t.Fatalf("legacy missing snapshot resumed: %v", err)
	}
	if err := f.store.FailBuild(ctx, badID, "legacy build cannot resume", false); err != nil {
		t.Fatal(err)
	}
	usable, err := f.store.HasUsableCurrent(ctx, f.kbID, f.workspaceID)
	if err != nil || !usable {
		t.Fatalf("legacy task failure downgraded current %v %v", usable, err)
	}
}

func TestPostgresSourceRevisionAndDuplicateFilenameSnapshot(t *testing.T) {
	f := newBuildFixture(t)
	ctx := context.Background()
	repo := NewPGXKnowledgeDocumentRepo(f.pool)
	var added []uuid.UUID
	for i := 0; i < 2; i++ {
		fileID, docID := uuid.New(), uuid.New()
		added = append(added, docID)
		f.exec(t, `INSERT INTO files(id,workspace_id,filename,original_filename,object_key,file_size,mime_type,sha256,file_category,status,created_by) VALUES($1,$2,'重复.xlsx','重复.xlsx',$3,12,'application/octet-stream',$4,'knowledge_document','active',$5)`, fileID, f.workspaceID, "fixture/"+fileID.String(), strings.Repeat("a", 64), f.actorID)
		if err := repo.Create(ctx, KnowledgeDocument{ID: docID, KnowledgeBaseID: f.kbID, WorkspaceID: f.workspaceID, FileID: fileID, Filename: "重复.xlsx", DocumentRole: DocumentRoleKnowledgeBase, Namespace: "xianyang", Status: KnowledgeDocumentStatusUploaded, CreatedBy: f.actorID}); err != nil {
			t.Fatal(err)
		}
	}
	c := f.create(t)
	var snapshot BuildInputSnapshot
	if err := json.Unmarshal([]byte(c.InputSnapshotJSON), &snapshot); err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Documents) != 4 || c.Version.SourceRevision != 2 {
		t.Fatalf("source creation revision/count lost %d %d", len(snapshot.Documents), c.Version.SourceRevision)
	}
	paths := map[string]bool{}
	for _, doc := range snapshot.Documents {
		if paths[doc.RelativePath] {
			t.Fatal("duplicate filename collided")
		}
		paths[doc.RelativePath] = true
	}
	if err := repo.SoftDelete(ctx, added[0]); err != nil {
		t.Fatal(err)
	}
	f.complete(t, c)
	kb, err := NewPGXKnowledgeBaseRepo(f.pool).GetByID(ctx, f.kbID)
	if err != nil || kb.SourceRevision != 3 || !kb.SourceDirty {
		t.Fatalf("publishing old snapshot hid later source edit %+v %v", kb, err)
	}
	var preserved bool
	if err := f.pool.QueryRow(ctx, `SELECT v.input_snapshot_canonical=$2 AND v.document_count=4 AND d.status='deleted' AND d.deleted_at IS NOT NULL FROM knowledge_index_versions v,knowledge_documents d WHERE v.id=$1 AND d.id=$3`, c.Version.ID, c.InputSnapshotJSON, added[0]).Scan(&preserved); err != nil || !preserved {
		t.Fatalf("published snapshot changed/deleted document resurrected %v %v", preserved, err)
	}
	usable, err := f.store.HasUsableCurrent(ctx, f.kbID, f.workspaceID)
	if err != nil || !usable {
		t.Fatal("dirty validated snapshot stopped serving")
	}
}
