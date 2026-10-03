package database

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"reflect"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"
)

//go:embed legacy_catalog.json
var legacyCatalogJSON []byte

//go:embed ledger_catalog.json
var ledgerCatalogJSON []byte

type catalogColumn struct {
	Name    string  `json:"name"`
	Type    string  `json:"type"`
	NotNull bool    `json:"not_null"`
	Default *string `json:"default"`
}

type catalogTable struct {
	Name        string            `json:"name"`
	Kind        string            `json:"kind"`
	RowSecurity bool              `json:"row_security"`
	Effects     bool              `json:"effects"`
	Columns     []catalogColumn   `json:"columns"`
	Constraints []string          `json:"constraints"`
	Unique      []json.RawMessage `json:"unique"`
}

type catalogSpec struct {
	Tables             []catalogTable `json:"tables"`
	UUIDExtensionValid bool           `json:"uuid_extension_valid"`
}

const catalogSQL = `SELECT jsonb_build_object(
 'uuid_extension_valid', (SELECT count(*) = 2 FROM pg_proc p
   JOIN pg_namespace n ON n.oid=p.pronamespace
   JOIN pg_depend d ON d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e'
   JOIN pg_extension e ON e.oid=d.refobjid
   WHERE e.extname='uuid-ossp' AND n.nspname='public' AND p.proname IN ('uuid_ns_url','uuid_generate_v5')),
 'tables', COALESCE((SELECT jsonb_agg(jsonb_build_object(
   'name', c.relname, 'kind', c.relkind::text, 'row_security', c.relrowsecurity,
   'effects', EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
       OR EXISTS(SELECT 1 FROM pg_rewrite r WHERE r.ev_class=c.oid),
   'columns', COALESCE((SELECT jsonb_agg(jsonb_build_object(
     'name', a.attname, 'type', format_type(a.atttypid,a.atttypmod), 'not_null', a.attnotnull,
     'default', CASE WHEN pg_get_expr(ad.adbin,ad.adrelid) LIKE 'nextval(%'
       AND EXISTS(SELECT 1 FROM pg_depend dp JOIN pg_class sq ON sq.oid=dp.objid AND sq.relkind='S'
         JOIN pg_depend ref ON ref.classid='pg_attrdef'::regclass AND ref.objid=ad.oid
          AND ref.refclassid='pg_class'::regclass AND ref.refobjid=sq.oid
         WHERE dp.classid='pg_class'::regclass AND dp.refobjid=c.oid AND dp.refobjsubid=a.attnum AND dp.deptype='a'
          AND pg_get_expr(ad.adbin,ad.adrelid)='nextval(' || quote_literal(sq.oid::regclass::text) || '::regclass)')
       THEN 'owned_nextval' ELSE pg_get_expr(ad.adbin,ad.adrelid) END
   ) ORDER BY a.attname) FROM pg_attribute a LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
     WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),'[]'::jsonb),
   'constraints', COALESCE((SELECT jsonb_agg(def ORDER BY def) FROM (
      SELECT DISTINCT pg_get_constraintdef(co.oid,true) || '|validated=' || co.convalidated::text
        || '|deferred=' || co.condeferrable::text || '/' || co.condeferred::text AS def
      FROM pg_constraint co WHERE co.conrelid=c.oid) q),'[]'::jsonb),
   'unique', COALESCE((SELECT jsonb_agg(spec ORDER BY spec::text) FROM (
     SELECT DISTINCT jsonb_build_object(
       'columns', ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,ord)
         JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum ORDER BY k.ord),
       'valid', i.indisvalid, 'ready', i.indisready, 'primary', i.indisprimary,
       'method', (SELECT am.amname FROM pg_class idx JOIN pg_am am ON am.oid=idx.relam WHERE idx.oid=i.indexrelid),
       'nulls_not_distinct', i.indnullsnotdistinct,
       'expression', pg_get_expr(i.indexprs,i.indrelid), 'predicate', pg_get_expr(i.indpred,i.indrelid)
     ) AS spec FROM pg_index i WHERE i.indrelid=c.oid AND i.indisunique) q),'[]'::jsonb)
 ) ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relname=ANY($1::text[])), '[]'::jsonb))`

func inspectCatalog(ctx context.Context, tx pgx.Tx, names []string) (catalogSpec, error) {
	var data []byte
	err := tx.QueryRow(ctx, catalogSQL, names).Scan(&data)
	var spec catalogSpec
	if err == nil {
		err = json.Unmarshal(data, &spec)
	}
	return spec, err
}

func catalogNames(data []byte) ([]string, error) {
	var spec catalogSpec
	if err := json.Unmarshal(data, &spec); err != nil {
		return nil, err
	}
	names := make([]string, 0, len(spec.Tables))
	for _, table := range spec.Tables {
		names = append(names, table.Name)
	}
	return names, nil
}

func verifyCatalog(ctx context.Context, tx pgx.Tx, data []byte) (catalogSpec, error) {
	var expected catalogSpec
	if err := json.Unmarshal(data, &expected); err != nil {
		return catalogSpec{}, err
	}
	names, err := catalogNames(data)
	if err != nil {
		return catalogSpec{}, err
	}
	actual, err := inspectCatalog(ctx, tx, names)
	if err != nil {
		return actual, err
	}
	if !actual.UUIDExtensionValid {
		return actual, fmt.Errorf("MIG_LEGACY_EXTENSION: trusted public uuid-ossp routines missing")
	}
	byName := make(map[string]catalogTable)
	for _, table := range actual.Tables {
		byName[table.Name] = table
	}
	for _, want := range expected.Tables {
		got, ok := byName[want.Name]
		if !ok {
			return actual, fmt.Errorf("MIG_PARTIAL_SCHEMA: required table %s missing", want.Name)
		}
		if got.Kind != want.Kind || got.RowSecurity != want.RowSecurity || got.Effects != want.Effects {
			return actual, fmt.Errorf("MIG_LEGACY_TABLE: %s kind, RLS or side effects differ", want.Name)
		}
		columns := make(map[string]catalogColumn)
		for _, column := range got.Columns {
			columns[column.Name] = column
		}
		for _, column := range want.Columns {
			found, ok := columns[column.Name]
			if !ok {
				return actual, fmt.Errorf("MIG_PARTIAL_SCHEMA: required column %s.%s missing", want.Name, column.Name)
			}
			if found.Type != column.Type || found.NotNull != column.NotNull || !sameDefault(found.Default, column.Default) {
				return actual, fmt.Errorf("MIG_LEGACY_COLUMN: %s.%s type/nullability/default differ", want.Name, column.Name)
			}
			delete(columns, column.Name)
		}
		for _, extra := range columns {
			if extra.NotNull && extra.Default == nil {
				return actual, fmt.Errorf("MIG_LEGACY_COLUMN: extra required column %s.%s prevents existing inserts", want.Name, extra.Name)
			}
		}
		if !reflect.DeepEqual(got.Constraints, want.Constraints) || !sameUnique(got.Unique, want.Unique) {
			return actual, fmt.Errorf("MIG_LEGACY_CONSTRAINT: %s PK/FK/unique/check semantics differ", want.Name)
		}
	}
	return actual, nil
}

func sameDefault(left, right *string) bool {
	if left == nil || right == nil {
		return left == nil && right == nil
	}
	normalize := func(value string) string {
		if value == "CURRENT_TIMESTAMP" || value == "transaction_timestamp()" {
			return "now()"
		}
		return strings.TrimSpace(value)
	}
	return normalize(*left) == normalize(*right)
}

func sameUnique(left, right []json.RawMessage) bool {
	normalize := func(values []json.RawMessage) []string {
		out := make([]string, 0, len(values))
		for _, value := range values {
			var object any
			_ = json.Unmarshal(value, &object)
			data, _ := json.Marshal(object)
			out = append(out, string(data))
		}
		sort.Strings(out)
		return out
	}
	return reflect.DeepEqual(normalize(left), normalize(right))
}
