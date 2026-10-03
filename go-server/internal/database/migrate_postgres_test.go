package database

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func testPostgres(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("VNEXT_TEST_POSTGRES_DSN")
	if dsn == "" {
		t.Skip("VNEXT_TEST_POSTGRES_DSN is required for real PostgreSQL migration tests")
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
	name := "vnext_ledger_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err = admin.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize()); err != nil {
		admin.Close()
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
	return pool
}

func baselineDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	entries, err := os.ReadDir("../../migrations")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if strings.HasSuffix(entry.Name(), ".sql") && entry.Name() < "000013" {
			data, err := os.ReadFile(filepath.Join("../../migrations", entry.Name()))
			if err != nil {
				t.Fatal(err)
			}
			if err = os.WriteFile(filepath.Join(dir, entry.Name()), data, 0600); err != nil {
				t.Fatal(err)
			}
		}
	}
	return dir
}

func execTest(t *testing.T, pool *pgxpool.Pool, sql string, args ...any) {
	t.Helper()
	if _, err := pool.Exec(context.Background(), sql, args...); err != nil {
		t.Fatal(err)
	}
}

func legacyFixture(t *testing.T, pool *pgxpool.Pool, dir string) {
	t.Helper()
	files, err := readMigrationFiles(dir)
	if err != nil {
		t.Fatal(err)
	}
	err = NewTxManager(pool).WithTx(context.Background(), func(ctx context.Context, tx pgx.Tx) error {
		for _, file := range files {
			if _, err := tx.Exec(ctx, file.up); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

func changedBusinessState(t *testing.T, pool *pgxpool.Pool) string {
	t.Helper()
	versionID := uuid.NewString()
	execTest(t, pool, `INSERT INTO knowledge_index_versions(id,knowledge_base_id,workspace_id,version,qdrant_collection,qdrant_namespace,status,document_count,chunk_count)
 SELECT $1,id,workspace_id,2,'retained_collection','xianyang','ready',42,987 FROM knowledge_bases WHERE namespace='xianyang'`, versionID)
	execTest(t, pool, `UPDATE knowledge_bases SET current_index_version_id=$1,status='stale',document_count=42,last_ingested_at='2020-01-02T03:04:05Z' WHERE namespace='xianyang'`, versionID)
	execTest(t, pool, `UPDATE files SET status='deleted',deleted_at='2020-01-02T03:04:05Z' WHERE id='638cb027-36f1-524d-afde-fc8752fd6608';
 UPDATE knowledge_documents SET status='deleted',deleted_at='2020-01-02T03:04:05Z' WHERE id='8d4d1ed4-227b-5c8e-84fc-8e96eebbcd91'`)
	return versionID
}

func assertBusinessState(t *testing.T, pool *pgxpool.Pool, versionID string) {
	t.Helper()
	var retained bool
	err := pool.QueryRow(context.Background(), `SELECT kb.current_index_version_id=$1 AND kb.status='stale' AND kb.document_count=42 AND kb.last_ingested_at='2020-01-02T03:04:05Z'
 AND f.deleted_at IS NOT NULL AND f.status='deleted' AND d.deleted_at IS NOT NULL AND d.status='deleted'
 FROM knowledge_bases kb, files f,knowledge_documents d WHERE kb.namespace='xianyang' AND f.id='638cb027-36f1-524d-afde-fc8752fd6608' AND d.id='8d4d1ed4-227b-5c8e-84fc-8e96eebbcd91'`, versionID).Scan(&retained)
	if err != nil || !retained {
		t.Fatalf("business state changed: retained=%v err=%v", retained, err)
	}
}

func TestPostgresMigrationsFreshRestartConcurrent(t *testing.T) {
	pool := testPostgres(t)
	dir := baselineDir(t)
	ctx := context.Background()
	var wg sync.WaitGroup
	errs := make(chan error, 4)
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); errs <- ApplyMigrations(ctx, pool, dir) }()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	var count int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM gongkan_schema_migrations WHERE origin='executed'`).Scan(&count); err != nil || count != 12 {
		t.Fatalf("ledger count %d %v", count, err)
	}
	id := changedBusinessState(t, pool)
	if err := ApplyMigrations(ctx, pool, dir); err != nil {
		t.Fatal(err)
	}
	assertBusinessState(t, pool, id)
	execTest(t, pool, `DELETE FROM gongkan_schema_migrations WHERE version=12`)
	if err := ApplyMigrations(ctx, pool, dir); err == nil || !strings.Contains(err.Error(), "MIG_PARTIAL_LEDGER") {
		t.Fatalf("truncated ledger accepted: %v", err)
	}
	assertBusinessState(t, pool, id)
}

func TestPostgresMigrationLockDeadlineRollsBackAndRetries(t *testing.T) {
	for _, callerDeadline := range []bool{false, true} {
		name := "batch_deadline"
		if callerDeadline {
			name = "shorter_caller_deadline"
		}
		t.Run(name, func(t *testing.T) {
			pool := testPostgres(t)
			dir := baselineDir(t)
			ctx := context.Background()
			holder, err := pool.Acquire(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer holder.Release()
			if _, err := holder.Exec(ctx, `SELECT pg_advisory_lock(1852076658,1)`); err != nil {
				t.Fatal(err)
			}
			defer func() { _, _ = holder.Exec(context.Background(), `SELECT pg_advisory_unlock(1852076658,1)`) }()
			migrationCtx := ctx
			if callerDeadline {
				var cancel context.CancelFunc
				migrationCtx, cancel = context.WithTimeout(ctx, 100*time.Millisecond)
				defer cancel()
			}
			started := time.Now()
			if callerDeadline {
				err = ApplyMigrations(migrationCtx, pool, dir)
			} else {
				err = applyMigrationsWithin(migrationCtx, pool, dir, 200*time.Millisecond)
			}
			if !errors.Is(err, context.DeadlineExceeded) || time.Since(started) > 2*time.Second {
				t.Fatalf("migration lock wait was not bounded: elapsed=%s err=%v", time.Since(started), err)
			}
			t.Logf("blocked advisory lock returned after %s with %v", time.Since(started), err)
			var ledger, adoption bool
			if err := pool.QueryRow(ctx, `SELECT to_regclass('public.gongkan_schema_migrations') IS NOT NULL,to_regclass('public.gongkan_schema_adoptions') IS NOT NULL`).Scan(&ledger, &adoption); err != nil || ledger || adoption {
				t.Fatalf("timed out lock wait left migration state: ledger=%v adoption=%v err=%v", ledger, adoption, err)
			}
			if _, err := holder.Exec(ctx, `SELECT pg_advisory_unlock(1852076658,1)`); err != nil {
				t.Fatal(err)
			}
			if err := ApplyMigrations(ctx, pool, dir); err != nil {
				t.Fatalf("migration did not recover after lock release: %v", err)
			}
			var count int
			if err := pool.QueryRow(ctx, `SELECT count(*) FROM gongkan_schema_migrations WHERE origin='executed'`).Scan(&count); err != nil || count != 12 {
				t.Fatalf("retry ledger count=%d err=%v", count, err)
			}
		})
	}
}

func TestPostgresMigrationSQLDeadlineRollsBackAndRetries(t *testing.T) {
	pool := testPostgres(t)
	dir := baselineDir(t)
	ctx := context.Background()
	if err := ApplyMigrations(ctx, pool, dir); err != nil {
		t.Fatal(err)
	}
	id := changedBusinessState(t, pool)
	pending := filepath.Join(dir, "000013_timeout.sql")
	if err := os.WriteFile(pending, []byte("-- +goose Up\nCREATE TABLE timeout_atomic_fixture(id integer); SELECT pg_sleep(10);"), 0600); err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	err := applyMigrationsWithin(ctx, pool, dir, 200*time.Millisecond)
	if !errors.Is(err, context.DeadlineExceeded) || time.Since(started) > 2*time.Second {
		t.Fatalf("migration SQL was not bounded: elapsed=%s err=%v", time.Since(started), err)
	}
	t.Logf("pending migration SQL returned after %s with %v", time.Since(started), err)
	var exists bool
	var count int
	if err := pool.QueryRow(ctx, `SELECT to_regclass('public.timeout_atomic_fixture') IS NOT NULL,(SELECT count(*) FROM gongkan_schema_migrations)`).Scan(&exists, &count); err != nil || exists || count != 12 {
		t.Fatalf("timed out SQL left partial DDL/ledger: exists=%v count=%d err=%v", exists, count, err)
	}
	assertBusinessState(t, pool, id)
	if err := os.WriteFile(pending, []byte("-- +goose Up\nCREATE TABLE timeout_atomic_fixture(id integer);"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := ApplyMigrations(ctx, pool, dir); err != nil {
		t.Fatalf("migration did not recover after SQL correction: %v", err)
	}
	if err := pool.QueryRow(ctx, `SELECT to_regclass('public.timeout_atomic_fixture') IS NOT NULL,(SELECT count(*) FROM gongkan_schema_migrations)`).Scan(&exists, &count); err != nil || !exists || count != 13 {
		t.Fatalf("retry did not commit complete pending migration: exists=%v count=%d err=%v", exists, count, err)
	}
	assertBusinessState(t, pool, id)
}

func TestPostgresMigrationsAdoptionNoReplay(t *testing.T) {
	pool := testPostgres(t)
	dir := baselineDir(t)
	legacyFixture(t, pool, dir)
	id := changedBusinessState(t, pool)
	// Compatible additions and equivalent timestamp default are allowed.
	execTest(t, pool, `ALTER TABLE knowledge_bases ADD COLUMN operator_note text; ALTER TABLE knowledge_bases ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP`)
	if err := ApplyMigrations(context.Background(), pool, dir); err != nil {
		t.Fatal(err)
	}
	assertBusinessState(t, pool, id)
	var count int
	err := pool.QueryRow(context.Background(), `SELECT count(*) FROM gongkan_schema_migrations m JOIN gongkan_schema_adoptions a ON a.id=m.adoption_id WHERE m.origin='adopted' AND a.profile='legacy-final-v12' AND a.baseline_version=12`).Scan(&count)
	if err != nil || count != 12 {
		t.Fatalf("adoption provenance %d %v", count, err)
	}
	if err := ApplyMigrations(context.Background(), pool, dir); err != nil {
		t.Fatal(err)
	}
	assertBusinessState(t, pool, id)
}

func TestPostgresMigrationsRejectMalformedLegacy(t *testing.T) {
	cases := map[string]string{
		"missing_table":     `DROP TABLE review_items`,
		"same_name_view":    `ALTER TABLE review_items RENAME TO review_items_hidden_fixture; CREATE VIEW review_items AS SELECT * FROM review_items_hidden_fixture`,
		"type":              `ALTER TABLE knowledge_index_versions ALTER COLUMN chunk_count TYPE bigint`,
		"default":           `ALTER TABLE knowledge_documents ALTER COLUMN status SET DEFAULT 'indexed'`,
		"nullability":       `ALTER TABLE run_events ALTER COLUMN sequence DROP NOT NULL`,
		"wrong_sequence":    `CREATE SEQUENCE wrong_sequence_fixture; ALTER TABLE run_events ALTER COLUMN sequence SET DEFAULT nextval('wrong_sequence_fixture')`,
		"changed_sequence":  `ALTER TABLE run_events ALTER COLUMN sequence SET DEFAULT nextval('run_events_sequence_seq')+1`,
		"required_addition": `ALTER TABLE knowledge_bases ADD COLUMN required_new text NOT NULL DEFAULT 'x'; ALTER TABLE knowledge_bases ALTER COLUMN required_new DROP DEFAULT`,
		"foreign_key":       `ALTER TABLE knowledge_documents DROP CONSTRAINT knowledge_documents_file_id_fkey`,
		"unvalidated_fk":    `ALTER TABLE knowledge_documents DROP CONSTRAINT knowledge_documents_file_id_fkey; ALTER TABLE knowledge_documents ADD FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE RESTRICT NOT VALID`,
		"unique":            `ALTER TABLE knowledge_bases DROP CONSTRAINT uq_knowledge_bases_workspace_name`,
		"extra_unique":      `CREATE UNIQUE INDEX bad_extra_unique ON knowledge_documents(filename)`,
		"extra_check":       `ALTER TABLE knowledge_documents ADD CHECK(filename<>'')`,
		"extra_trigger":     `CREATE FUNCTION extra_effect_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$; CREATE TRIGGER extra_effect_fixture BEFORE UPDATE ON knowledge_bases FOR EACH ROW EXECUTE FUNCTION extra_effect_fixture()`,
		"rls":               `ALTER TABLE knowledge_bases ENABLE ROW LEVEL SECURITY`,
		"wrong_current":     `UPDATE knowledge_bases SET current_index_version_id='ffffffff-ffff-ffff-ffff-ffffffffffff' WHERE namespace='xianyang'`,
	}
	for name, sql := range cases {
		t.Run(name, func(t *testing.T) {
			pool := testPostgres(t)
			dir := baselineDir(t)
			legacyFixture(t, pool, dir)
			execTest(t, pool, sql)
			if err := ApplyMigrations(context.Background(), pool, dir); err == nil {
				t.Fatal("malformed legacy accepted")
			}
			var ledger bool
			if err := pool.QueryRow(context.Background(), `SELECT to_regclass('public.gongkan_schema_migrations') IS NOT NULL`).Scan(&ledger); err != nil || ledger {
				t.Fatalf("failed adoption mutated schema %v %v", ledger, err)
			}
		})
	}
}

func TestPostgresAdoptionForwardPreservesUserPointerAndDeletes(t *testing.T) {
	for _, rollback := range []bool{false, true} {
		name := "v2"
		if rollback {
			name = "explicit_v1_rollback"
		}
		t.Run(name, func(t *testing.T) {
			pool := testPostgres(t)
			dir := baselineDir(t)
			legacyFixture(t, pool, dir)
			id := changedBusinessState(t, pool)
			if rollback {
				if err := pool.QueryRow(context.Background(), `SELECT v.id FROM knowledge_index_versions v JOIN knowledge_bases kb ON kb.id=v.knowledge_base_id WHERE kb.namespace='xianyang' AND v.version=1`).Scan(&id); err != nil {
					t.Fatal(err)
				}
				execTest(t, pool, `UPDATE knowledge_bases SET current_index_version_id=$1 WHERE namespace='xianyang'`, id)
			}
			data, err := os.ReadFile("../../migrations/000013_versioned_knowledge_publication.sql")
			if err != nil {
				t.Fatal(err)
			}
			if err = os.WriteFile(filepath.Join(dir, "000013_versioned_knowledge_publication.sql"), data, 0600); err != nil {
				t.Fatal(err)
			}
			if err := ApplyMigrations(context.Background(), pool, dir); err != nil {
				t.Fatal(err)
			}
			assertBusinessState(t, pool, id)
			if err := ApplyMigrations(context.Background(), pool, dir); err != nil {
				t.Fatal(err)
			}
			assertBusinessState(t, pool, id)
			var adopted, executed, declared, validated int
			err = pool.QueryRow(context.Background(), `SELECT (SELECT count(*) FROM gongkan_schema_migrations WHERE origin='adopted'),(SELECT count(*) FROM gongkan_schema_migrations WHERE origin='executed'),(SELECT count(*) FROM knowledge_index_versions WHERE status='ready' AND storage_contract='legacy_unversioned' AND validation_state='legacy_declared_ready'),(SELECT count(*) FROM knowledge_index_versions WHERE validation_state='validated')`).Scan(&adopted, &executed, &declared, &validated)
			if err != nil || adopted != 12 || executed != 1 || declared == 0 || validated != 0 {
				t.Fatalf("adoption/forward provenance %d %d %d %d %v", adopted, executed, declared, validated, err)
			}
		})
	}
}

func TestPostgresFreshBatchFailureRollsBackBootstrap(t *testing.T) {
	pool := testPostgres(t)
	dir := baselineDir(t)
	if err := os.WriteFile(filepath.Join(dir, "000013_failure.sql"), []byte("-- +goose Up\nSELECT 1/0;"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := ApplyMigrations(context.Background(), pool, dir); err == nil {
		t.Fatal("fresh failing batch accepted")
	}
	var count int
	if err := pool.QueryRow(context.Background(), `SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p')`).Scan(&count); err != nil || count != 0 {
		t.Fatalf("fresh failed batch left tables: %d %v", count, err)
	}
}

func TestPostgresMigrationsChecksumAndAtomicPending(t *testing.T) {
	pool := testPostgres(t)
	dir := baselineDir(t)
	ctx := context.Background()
	if err := ApplyMigrations(ctx, pool, dir); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(dir, "000001_init.sql")
	data, _ := os.ReadFile(file)
	if err := os.WriteFile(file, append(data, []byte("\n-- changed\n")...), 0600); err != nil {
		t.Fatal(err)
	}
	if err := ApplyMigrations(ctx, pool, dir); err == nil || !strings.Contains(err.Error(), "MIG_HISTORY_CHECKSUM") {
		t.Fatalf("changed history accepted %v", err)
	}
	if err := os.WriteFile(file, data, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "000013_atomic.sql"), []byte("-- +goose Up\nCREATE TABLE atomic_first(id int);"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "000014_failure.sql"), []byte("-- +goose Up\nSELECT 1/0;"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := ApplyMigrations(ctx, pool, dir); err == nil {
		t.Fatal("failing batch accepted")
	}
	var exists bool
	var count int
	if err := pool.QueryRow(ctx, `SELECT to_regclass('public.atomic_first') IS NOT NULL,(SELECT count(*) FROM gongkan_schema_migrations)`).Scan(&exists, &count); err != nil || exists || count != 12 {
		t.Fatalf("batch was not atomic: exists=%v count=%d err=%v", exists, count, err)
	}
}
