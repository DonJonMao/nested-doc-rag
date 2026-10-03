package database

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const ledgerSQL = `CREATE TABLE public.gongkan_schema_adoptions (
 id uuid PRIMARY KEY, profile text NOT NULL, baseline_version bigint NOT NULL,
 schema_fingerprint text NOT NULL, binary_revision text NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now(), report_json jsonb NOT NULL);
CREATE TABLE public.gongkan_schema_migrations (
 version bigint PRIMARY KEY CHECK(version>0), filename text NOT NULL UNIQUE,
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 recorded_at timestamptz NOT NULL DEFAULT now(), origin text NOT NULL CHECK(origin IN ('executed','adopted')),
 adoption_id uuid REFERENCES public.gongkan_schema_adoptions(id),
 CHECK((origin='adopted' AND adoption_id IS NOT NULL) OR (origin='executed' AND adoption_id IS NULL)));`

type migrationFile struct {
	version int64
	name    string
	sha256  string
	up      string
}

var historicalChecksums = map[int64]string{
	1:  "f52670c72b385e8792ff7a90fcab3cba294668a13ceaf50873c255c732d265c7",
	2:  "8120a568ff75f1bca7019272a75ffa79a62d1efcd5dd57e19335547a87f165bd",
	3:  "440eefd4045afecaf1b695a4d383243c7eeea3aad4f86fbd47b8c7562aae2611",
	4:  "9fd285098bf024b5f7a1d0a72b548103d8a893a0307175f4cdefd6c3b3764eaf",
	5:  "be2f66b3b89aa808d9e266e4078bb10458640e8aae31359ff60f83d9db23b78c",
	6:  "ea75cfdf26b9886c35e6bc27a2d04a6212b0e8797100687d249fceb92d429c14",
	7:  "87d008c5585a9e92b47ed1a46fbe43bade8b5cc8658f57a02e0a7ceb0fe55b90",
	8:  "de19b97e877e059b117f239d10eeb35ac6c0a56ee1aded16c620cc7e43dd4d58",
	9:  "04300222ffe55eafbc25f312b9a28993af3483c47dc31e5e4d84bb4fc7d148fb",
	10: "60c7caa03d847ab1270b328d425b5ead99663724206c3d96d7b90867f92561bd",
	11: "b41077d48a117b0c6b641f572469a80fd6db95d7917ed17dd703f02e41ea1069",
	12: "c2840748d4d038904d963fc33b0799d2869c04abef88ce578884cb46eaee79a7",
}

var historicalFilenames = map[int64]string{
	1: "000001_init.sql", 2: "000002_auth_workspace.sql", 3: "000003_files_artifacts.sql",
	4: "000004_jobs_events.sql", 5: "000005_form_fill_runs.sql", 6: "000006_knowledge_ingestion.sql",
	7: "000007_review_items.sql", 8: "000008_productized_knowledge_fill.sql", 9: "000009_fill_run_name.sql",
	10: "000010_review_writeback_evidence.sql", 11: "000011_global_knowledge_base.sql", 12: "000012_preprocessed_knowledge_seed.sql",
}

// This bounds database lock acquisition and SQL execution for every startup
// caller. context.WithTimeout retains an earlier caller deadline.
const migrationBatchTimeout = 30 * time.Second

func ApplyMigrations(ctx context.Context, pool *pgxpool.Pool, dir string) error {
	return applyMigrationsWithin(ctx, pool, dir, migrationBatchTimeout)
}

func applyMigrationsWithin(ctx context.Context, pool *pgxpool.Pool, dir string, timeout time.Duration) error {
	if pool == nil {
		return fmt.Errorf("postgres pool is nil")
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	files, err := readMigrationFiles(dir)
	if err != nil {
		return err
	}
	return NewTxManager(pool).WithTx(ctx, func(ctx context.Context, tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(1852076658,1); SET LOCAL search_path=public,pg_catalog`); err != nil {
			return err
		}
		var hasLedger, hasAdoptions bool
		if err := tx.QueryRow(ctx, `SELECT to_regclass('public.gongkan_schema_migrations') IS NOT NULL,
		 to_regclass('public.gongkan_schema_adoptions') IS NOT NULL`).Scan(&hasLedger, &hasAdoptions); err != nil {
			return err
		}
		if hasLedger != hasAdoptions {
			return fmt.Errorf("MIG_PARTIAL_SCHEMA: migration ledger/adoption pair is incomplete")
		}
		if !hasLedger {
			names, err := catalogNames(legacyCatalogJSON)
			if err != nil {
				return err
			}
			actual, err := inspectCatalog(ctx, tx, names)
			if err != nil {
				return err
			}
			if len(actual.Tables) > 0 {
				for _, table := range actual.Tables {
					if _, err := tx.Exec(ctx, "LOCK TABLE "+pgx.Identifier{"public", table.Name}.Sanitize()+" IN SHARE ROW EXCLUSIVE MODE"); err != nil {
						return err
					}
				}
				actual, err = verifyCatalog(ctx, tx, legacyCatalogJSON)
				if err != nil {
					return err
				}
				if err := verifyCurrentOwnership(ctx, tx); err != nil {
					return err
				}
				if _, err := tx.Exec(ctx, ledgerSQL); err != nil {
					return err
				}
				report, _ := json.Marshal(actual)
				digest := sha256.Sum256(report)
				adoptionID := uuid.New()
				if _, err := tx.Exec(ctx, `INSERT INTO gongkan_schema_adoptions
				 (id,profile,baseline_version,schema_fingerprint,binary_revision,report_json)
				 VALUES($1,'legacy-final-v12',12,$2,'ledger-v1',$3)`, adoptionID, hex.EncodeToString(digest[:]), report); err != nil {
					return err
				}
				for _, file := range files {
					if file.version <= 12 {
						if err := recordMigration(ctx, tx, file, "adopted", &adoptionID); err != nil {
							return err
						}
					}
				}
			} else if _, err := tx.Exec(ctx, ledgerSQL); err != nil {
				return err
			}
		} else if _, err := verifyCatalog(ctx, tx, ledgerCatalogJSON); err != nil {
			return fmt.Errorf("MIG_LEDGER_SCHEMA: %w", err)
		}
		applied, err := verifiedMigrationPrefix(ctx, tx, files)
		if err != nil {
			return err
		}
		// An existing ledger can only come from a committed complete baseline.
		// Replaying seeds after someone truncates the ledger would corrupt data.
		if hasLedger && applied < 12 {
			return fmt.Errorf("MIG_PARTIAL_LEDGER: existing ledger has only %d baseline entries", applied)
		}
		for _, file := range files[applied:] {
			if _, err := tx.Exec(ctx, file.up); err != nil {
				return fmt.Errorf("apply migration %q: %w", file.name, err)
			}
			if err := recordMigration(ctx, tx, file, "executed", nil); err != nil {
				return err
			}
		}
		return nil
	})
}

func readMigrationFiles(dir string) ([]migrationFile, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, fmt.Errorf("read migrations dir %q: %w", dir, err)
	}
	var files []migrationFile
	pattern := regexp.MustCompile(`^(\d+)_.*\.sql$`)
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".sql") {
			continue
		}
		match := pattern.FindStringSubmatch(entry.Name())
		if match == nil {
			return nil, fmt.Errorf("MIG_HISTORY_NAME: invalid migration %s", entry.Name())
		}
		version, err := strconv.ParseInt(match[1], 10, 64)
		if err != nil {
			return nil, err
		}
		data, err := os.ReadFile(filepath.Join(dir, entry.Name()))
		if err != nil {
			return nil, err
		}
		digest := sha256.Sum256(data)
		checksum := hex.EncodeToString(digest[:])
		if expected, ok := historicalFilenames[version]; ok && entry.Name() != expected {
			return nil, fmt.Errorf("MIG_HISTORY_NAME: frozen migration filename %s changed", expected)
		}
		if expected, ok := historicalChecksums[version]; ok && checksum != expected {
			return nil, fmt.Errorf("MIG_HISTORY_CHECKSUM: frozen migration %s changed", entry.Name())
		}
		if version > 12 && !strings.Contains(string(data), "-- +goose Up") {
			return nil, fmt.Errorf("MIG_HISTORY_SECTION: new migration %s requires explicit Up", entry.Name())
		}
		up := gooseUpSection(string(data))
		if strings.TrimSpace(up) == "" {
			return nil, fmt.Errorf("MIG_HISTORY_SECTION: empty Up in %s", entry.Name())
		}
		files = append(files, migrationFile{version: version, name: entry.Name(), sha256: checksum, up: up})
	}
	sort.Slice(files, func(i, j int) bool { return files[i].version < files[j].version })
	if len(files) < 12 {
		return nil, fmt.Errorf("MIG_HISTORY_GAP: historical baseline incomplete")
	}
	for index, file := range files {
		if file.version != int64(index+1) {
			return nil, fmt.Errorf("MIG_HISTORY_GAP: missing or duplicate version at %d", index+1)
		}
	}
	return files, nil
}

func recordMigration(ctx context.Context, tx pgx.Tx, file migrationFile, origin string, adoptionID *uuid.UUID) error {
	_, err := tx.Exec(ctx, `INSERT INTO gongkan_schema_migrations(version,filename,sha256,origin,adoption_id) VALUES($1,$2,$3,$4,$5)`, file.version, file.name, file.sha256, origin, adoptionID)
	return err
}

func verifiedMigrationPrefix(ctx context.Context, tx pgx.Tx, files []migrationFile) (int, error) {
	rows, err := tx.Query(ctx, `SELECT version,filename,sha256 FROM gongkan_schema_migrations ORDER BY version`)
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	count := 0
	for rows.Next() {
		var version int64
		var name, checksum string
		if err := rows.Scan(&version, &name, &checksum); err != nil {
			return 0, err
		}
		if count >= len(files) || version != int64(count+1) || name != files[count].name || checksum != files[count].sha256 {
			return 0, fmt.Errorf("MIG_HISTORY_CHECKSUM: ledger gap, unknown version or changed file at version %d", version)
		}
		count++
	}
	return count, rows.Err()
}

func verifyCurrentOwnership(ctx context.Context, tx pgx.Tx) error {
	var bad int
	err := tx.QueryRow(ctx, `SELECT count(*) FROM knowledge_bases kb LEFT JOIN knowledge_index_versions v ON v.id=kb.current_index_version_id
	 WHERE kb.current_index_version_id IS NOT NULL AND (v.id IS NULL OR v.knowledge_base_id<>kb.id OR v.workspace_id<>kb.workspace_id)`).Scan(&bad)
	if err != nil {
		return err
	}
	if bad != 0 {
		return fmt.Errorf("MIG_CURRENT_OWNERSHIP: %d invalid current version pointers", bad)
	}
	return nil
}

func gooseUpSection(sql string) string {
	if idx := strings.Index(sql, "-- +goose Down"); idx >= 0 {
		sql = sql[:idx]
	}
	sql = strings.ReplaceAll(sql, "-- +goose Up", "")
	return sql
}
