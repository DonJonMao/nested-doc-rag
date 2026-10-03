# Phase 4B：迁移账本、历史数据库采用与版本发布设计

**状态：Phase 4B 已进入授权实施，迁移与 Go 领域实现通过真实 PostgreSQL 测试。** 本文记录已实现的窄范围协议；整条 API→Redis→Python→Qdrant 链路的最终验收由独立证据记录决定，不能从这里的 PG receipt fixture 推断真实 Qdrant 已验证。本阶段不实现 GC 或通用 dispatch/event outbox。

设计依据为 [实现规格 §10](NESTED_DOC_RAG_VNEXT_IMPLEMENTATION.md)、[K01 验收项](ACCEPTANCE.md)、冻结的 `go-server/migrations/000001–000012` 与新增 `000013_versioned_knowledge_publication.sql`。前五节保留历史基线的完整 catalog contract，后续章节描述实际生产接口及本阶段限制。

## 1. 必须先消除的历史风险

旧 `ApplyMigrations` 在每次 API、worker 启动时执行所有文件的 Up；`schema_migrations_marker` 只是一张表，没有迁移执行账本。新的 [ApplyMigrations](../../go-server/internal/database/migrate.go) 使用受锁账本、冻结文件 checksum 与明确 legacy adoption。不能把旧 marker 的存在或行数视为已经应用到某个版本。

`000012_preprocessed_knowledge_seed.sql` 的 Up 会：

- 改写旧 namespace 及已有 fill run 的 target namespace；
- upsert seed KB，重置其状态、计数和时间；
- 将 seed file 恢复为 active、清除 `deleted_at`；
- 将 seed document 恢复为 indexed、清除 `deleted_at`；
- 改写 ordinal version 1 的 collection、namespace、状态和计数；
- 无条件将 seed KB 的 `current_index_version_id` 指向 version 1。

因此，一次普通重启就可能覆盖已发布 V2、用户选择的回滚版本或删除状态。`000008`、`000011` 也含有业务 seed upsert。不能通过“再执行一次以补齐结构”采用老数据库。

迁移账本只能防止未来覆盖；它不能恢复已被旧启动逻辑覆盖的 current 指针。不能用 `MAX(version)` 猜测用户希望的活跃版本；恢复须依据已有审计/备份另行处理。

## 2. 历史迁移的冻结边界

| 版本 | 结构变化 | 数据变化及采用处理 |
| --- | --- | --- |
| 1 | `uuid-ossp`、marker | marker 不证明执行历史 |
| 2 | auth、workspace、audit 共 7 张表 | 默认角色；adoption 不重新 seed |
| 3 | files、run_artifacts | 无业务 seed |
| 4 | jobs、run_events | sequence 为 BIGSERIAL，实际 NOT NULL |
| 5 | form_files、fill_runs | KB/version 列当时没有 FK |
| 6 | KB、document、version、ingestion 共 4 张表 | current version 没有 FK |
| 7 | review_items | 没有 `(run_id, field_id)` 唯一约束 |
| 8 | KB namespace/status/count、document 时间、fill sequence、唯一事件索引 | namespace/sequence backfill、默认 workspace/KB upsert |
| 9 | fill_runs.name | NOT NULL，默认空字符串 |
| 10 | review writeback/evidence 列 | 文件无 goose 标记；历史兼容下整份文件是 Up |
| 11 | 无新增结构 | 所有 workspace 的 global KB seed；schema 无法证明它执行过 |
| 12 | 无永久结构；只有事务内临时 seed 表 | namespace 改写及 seed 重置；schema 无法证明它执行过 |

历史文件 1–12 以继承提交 `3a5a799` 的字节为冻结边界，修复通过 13 及后续 forward migration 进行。精确初始基线 `0303000` 仅含 1–10：继承阶段修改了 8 的城东 seed 名称/namespace，并加入 11、12；因此不能声称 1–12 全部与初始基线逐字一致。Phase 4B 未改写这些冻结文件。不能改写 12 的 Up 后声称老数据库已执行同一版本的新逻辑。新文件必须有唯一数字版本和明确 Up；当前 10 的无标记形式只列为冻结的历史特例。启动程序永不自动执行 Down。

### 2.1 冻结的完整文件 SHA-256

校验包含整个 SQL 文件的原始 UTF-8 字节，包括注释、换行和 Down；不做空白归一化。SQL 提取结果与校验范围分别记录，避免不同文件共用一个经过清洗的 checksum。

| 文件 | SHA-256 |
| --- | --- |
| `000001_init.sql` | `f52670c72b385e8792ff7a90fcab3cba294668a13ceaf50873c255c732d265c7` |
| `000002_auth_workspace.sql` | `8120a568ff75f1bca7019272a75ffa79a62d1efcd5dd57e19335547a87f165bd` |
| `000003_files_artifacts.sql` | `440eefd4045afecaf1b695a4d383243c7eeea3aad4f86fbd47b8c7562aae2611` |
| `000004_jobs_events.sql` | `9fd285098bf024b5f7a1d0a72b548103d8a893a0307175f4cdefd6c3b3764eaf` |
| `000005_form_fill_runs.sql` | `be2f66b3b89aa808d9e266e4078bb10458640e8aae31359ff60f83d9db23b78c` |
| `000006_knowledge_ingestion.sql` | `ea75cfdf26b9886c35e6bc27a2d04a6212b0e8797100687d249fceb92d429c14` |
| `000007_review_items.sql` | `87d008c5585a9e92b47ed1a46fbe43bade8b5cc8658f57a02e0a7ceb0fe55b90` |
| `000008_productized_knowledge_fill.sql` | `de19b97e877e059b117f239d10eeb35ac6c0a56ee1aded16c620cc7e43dd4d58` |
| `000009_fill_run_name.sql` | `04300222ffe55eafbc25f312b9a28993af3483c47dc31e5e4d84bb4fc7d148fb` |
| `000010_review_writeback_evidence.sql` | `60c7caa03d847ab1270b328d425b5ead99663724206c3d96d7b90867f92561bd` |
| `000011_global_knowledge_base.sql` | `b41077d48a117b0c6b641f572469a80fd6db95d7917ed17dd703f02e41ea1069` |
| `000012_preprocessed_knowledge_seed.sql` | `c2840748d4d038904d963fc33b0799d2869c04abef88ce578884cb46eaee79a7` |

实现中需要受版本控制的 baseline manifest；不能在无账本的首次启动时，把随手改过的磁盘文件当作可信历史。上表只覆盖当前受审阅的 baseline，其他历史发布必须有单独、可测试的兼容 profile。

## 3. 迁移账本与启动事务

### 3.1 待新增的账本结构

账本 bootstrap 由新的 migration engine 在同一个受锁事务中创建，不依赖已经存在的迁移账本来执行“创建账本”的 SQL。名称明确与旧 marker 分离。

| 表 | 待新增列/约束 |
| --- | --- |
| `gongkan_schema_migrations` | `version bigint NOT NULL PRIMARY KEY`，正数；`filename text NOT NULL UNIQUE`；`sha256 text NOT NULL`，64 位小写 hex；`recorded_at timestamptz NOT NULL DEFAULT now()`；`origin text NOT NULL`，仅 `executed/adopted`；`adoption_id uuid NULL`，FK 至 adoption 表；origin 与 adoption_id 的匹配 CHECK |
| `gongkan_schema_adoptions` | `id uuid PRIMARY KEY`；`profile text NOT NULL`；`baseline_version bigint NOT NULL`；`schema_fingerprint text NOT NULL`；`binary_revision text NOT NULL`；`recorded_at timestamptz NOT NULL DEFAULT now()`；`report_json jsonb NOT NULL` |

adoption 的时间是“此时接受该 baseline”，不是捏造历史 applied_at。报告只存 catalog 结构、profile、计数与差异摘要，不记录密码、JWT、原文、业务对象内容或完整数据库行。

已经存在的同名账本也必须先校验列类型、nullability、PK/FK/unique/CHECK；`CREATE TABLE IF NOT EXISTS` 不能证明它满足账本协议。

### 3.2 同一事务完成锁定、校验、执行和记账

使用 [TxManager.WithTx](../../go-server/internal/database/tx.go) 承载一个 migration batch；同一 `pgx.Tx` 中：

1. 取得 `pg_advisory_xact_lock`。使用固定、文档化的应用专用 int32 pair；API 与 worker 共用相同常量，不能由进程 PID、随机值或本机目录生成。
2. 在固定应用 schema `public` 下检查 catalog，分类 fresh、legacy adoption、已受账本管理；不能从任意 search_path 中找到同名表就接受。
3. 校验冻结历史 manifest、ledger 连续性、每行 filename/checksum。拒绝重复版本、缺失文件、版本缺口、未知较新版本和 checksum 不符。
4. 对 legacy adoption 校验完整兼容 profile，再插入 adoption 及 1–12 的 `origin=adopted` 行，**不执行任何历史 Up**。
5. 对 fresh 数据库依序执行 1–12，每份 SQL 成功后插入 `origin=executed`；对受管理数据库只执行未记录的后续版本。
6. 执行当前发布包含的 forward migration，随后提交整个 batch。

任何 SQL、catalog 校验或 ledger insert 失败，整个 batch 的 DDL、DML、ledger/adoption 都回滚；事务结束自动释放 advisory lock。统一 `ApplyMigrations` 入口为数据库锁等待及 SQL 执行设置固定 30 秒整批期限，调用方更短的 context deadline 优先生效；API/worker 无需额外配置。超时错误向启动层返回，不能在失败后继续启动 API/worker。该期限约束数据库操作，不宣称文件系统读取具有可中断超时。

这个锁只协调采用新 engine 的迁移进程。legacy API/worker 的 DML 和旧迁移函数不会自动遵守它。正式切换时须停止旧写入节点，再同时替换 API/worker；不能在旧二进制仍可能启动的环境做混合滚动升级。旧 binary rollback 也可能重播 12，应使用保留新 migration engine 的兼容版本；数据回滚走版本激活接口，不走历史 Down。

所有启动 migration 必须能在 PostgreSQL 事务内完成。`CREATE INDEX CONCURRENTLY`、外部 Qdrant/Redis/对象存储访问、模型请求不能放入此 batch。未来需要非事务 DDL 时单独设计运维步骤，不能偷偷削弱这里的原子保证。

## 4. Legacy adoption 判定

只支持一个初始 profile：`legacy-final-v12`。它是历史 1–10 的完整最终结构，加上明确跳过 11–12 数据 seed 的采用策略，不是“证明这 12 份 SQL 都曾执行”。

| 观察状态 | 处理 |
| --- | --- |
| 无 ledger，应用 schema 无历史业务表或 marker | fresh；执行历史 Up 一次 |
| 无 ledger，19 张永久表完整满足下文 required contract | adoption；1–12 全部标 adopted，保留全部业务数据 |
| 只有 marker、部分表、缺 required 列/约束、错误类型/nullability/default | 拒绝采用，给出逐项差异；不能先补表/回放 seed |
| 有正确 ledger | 校验历史 checksum，只执行未记录的 forward migration |
| 有不兼容 ledger、未知较新版本、历史 checksum 变更 | 拒绝启动；不自动删除/重建账本 |

部分老结构不做自动 prefix 推断。若将来必须支持早期 release，需新增具有可信 manifest、catalog contract 和 forward 修复的独立 profile。11、12 没有永久结构，因此“没有 global/seed 行”不表示未执行，“存在 seed 行”也不表示可以重播；删除 seed 文档、回滚到 V1、保留 V2/current 都是合法数据状态。

采用时，对 required 表按稳定顺序取表锁以冻结 catalog/数据观察，先校验再只写新账本；不修改 current、版本状态、namespace、文件/文档状态或 deleted_at。若已有 current UUID 悬空，或指向不同 KB/workspace，返回独立的数据一致性诊断；forward migration 的 ownership FK 不能靠猜测 current 来通过。

## 5. 完整 required schema contract

下面是历史 1–12 后的 **19 张永久表**；12 的两个临时 seed 表不是 contract 的一部分。列名来自实际文件：第 8 份增加的是 `last_event_sequence`，第 10 份无标记 ALTER 增加的 4 列也包含在内。

### 5.1 类型、nullability 与 default

记号：`!` 表示 NOT NULL，`?` 表示 nullable；`=x` 是必需 default，没有 `=` 的列要求无 default。PK 隐含 NOT NULL。`T`=`text`、`U`=`uuid`、`I`=`integer/int4`、`L`=`bigint/int8`、`Z`=`timestamp with time zone/timestamptz`、`B`=`boolean`、`J`=`jsonb`、`F`=`double precision/float8`。类型按 PostgreSQL 内置 OID、typmod 比较，不接受未经 profile 声明的 varchar、domain、numeric、json 或 timestamp-without-time-zone 替代。

```text
schema_migrations_marker
  id L!=owned nextval; name T!; applied_at Z!=now()

users
  id U!; username T!; password_hash T!; display_name T?; email T?
  status T!='active'; created_at Z!=now(); updated_at Z!=now()
roles
  id U!; name T!; description T?; created_at Z!=now()
user_roles
  user_id U!; role_id U!
refresh_tokens
  id U!; user_id U!; token_hash T!; expires_at Z!; revoked_at Z?
  created_at Z!=now()
workspaces
  id U!; name T!; description T?; created_by U?
  created_at Z!=now(); updated_at Z!=now()
workspace_members
  workspace_id U!; user_id U!; role T!; created_at Z!=now()
audit_logs
  id U!; workspace_id U?; user_id U?; action T!; resource_type T?
  resource_id T?; ip T?; user_agent T?; payload_json J?; created_at Z!=now()

files
  id U!; workspace_id U!; filename T!; original_filename T!; object_key T!
  file_size L!; mime_type T?; sha256 T!; file_category T!
  status T!='active'; created_by U?; created_at Z!=now(); deleted_at Z?
run_artifacts
  id U!; workspace_id U!; run_id U!; artifact_type T!; filename T!; object_key T!
  local_path T?; content_type T?; file_size L?; sha256 T?; created_by U?
  created_at Z!=now()

jobs
  id U!; workspace_id U!; job_type T!; resource_type T!; resource_id U!; status T!
  priority I!=0; attempt I!=0; max_attempts I!=3; payload_json J!={}
  error_message T?; cancel_requested_at Z?; queued_at Z?; started_at Z?
  heartbeat_at Z?; finished_at Z?; created_by U?
  created_at Z!=now(); updated_at Z!=now()
run_events
  id U!; workspace_id U!; run_id U!; job_id U?; event_type T!
  sequence L!=owned nextval; payload_json J!={}; created_at Z!=now()

form_files
  id U!; workspace_id U!; file_id U!; filename T!; created_by U?; created_at Z!=now()
fill_runs
  id U!; workspace_id U!; form_file_id U!; job_id U?; knowledge_base_id U?
  index_version_id U?; target_namespace T!; global_namespace T!='global'
  room_context T?; rows_spec T!; retrieval_mode T!='layered'
  prompt_version T!='step15_compat'; judge_enabled B!=false; use_judge_cache B!=false
  writeback_enabled B!=true; status T!='created'; progress_total I!=0; progress_done I!=0
  out_dir T?; run_manifest_path T?; summary_path T?; filled_form_artifact_id U?
  answered_count I!=0; partial_clue_count I!=0; not_found_count I!=0
  conflict_unresolved_count I!=0; review_required_count I!=0
  writeback_allowed_count I!=0; failed_count I!=0; error_message T?; created_by U?
  created_at Z!=now(); queued_at Z?; started_at Z?; finished_at Z?; updated_at Z!=now()
  last_event_sequence L!=0; name T!=''

knowledge_bases
  id U!; workspace_id U!; name T!; description T?; qdrant_collection T?
  current_index_version_id U?; created_by U?; created_at Z!=now(); updated_at Z!=now()
  namespace T!; status T!='empty'; last_ingested_at Z?; document_count I!=0
knowledge_documents
  id U!; knowledge_base_id U!; workspace_id U!; file_id U!; filename T!
  document_role T!; namespace T!; status T!='uploaded'; created_by U?
  created_at Z!=now(); updated_at Z!=now(); deleted_at Z?; last_ingested_at Z?
knowledge_index_versions
  id U!; knowledge_base_id U!; workspace_id U!; version I!; qdrant_collection T!
  qdrant_namespace T?; artifact_dir T?; manifest_path T?; status T!='building'
  document_count I!=0; chunk_count I!=0; created_by U?; created_at Z!=now()
  ready_at Z?; failed_at Z?; error_message T?
ingestion_jobs
  id U!; workspace_id U!; knowledge_base_id U!; index_version_id U?; job_id U?
  status T!='created'; progress I!=0; document_count I!=0; error_message T?
  python_command T?; out_dir T?; started_at Z?; finished_at Z?; created_by U?
  created_at Z!=now(); updated_at Z!=now()

review_items
  id U!; workspace_id U!; run_id U!; field_id T?; row_index I?; target_cell T?
  question_text T?; answer_status T?; answer_value T?; confidence F?
  source_chunk_ids J!=[]; evidence_attachment_ids J!=[]; reference_chunk_ids J!=[]
  reference_source_documents J!=[]; reference_snippets J!=[]; critic_flags J!=[]
  risk_level T!='medium'; review_required B!=true; writeback_allowed B!=false
  suggested_status T?; suggested_answer_value T?; suggested_reference_source_documents J!=[]
  reasons J!=[]; status T!='pending'; reviewer_id U?; reviewed_at Z?
  review_comment T?; edited_answer T?; raw_payload J!={}; overlay_payload J!={}
  created_at Z!=now(); updated_at Z!=now(); writeback_status T?; writeback_action T?
  evidence_refs J!=[]; writeback_error_code T?
```

BIGSERIAL 要反射为 bigint + NOT NULL + 精确引用本列 owned sequence 的 `nextval` default；不能只检测类型或把 SQL 中未写 `NOT NULL` 的 `run_events.sequence` 当 nullable。sequence 的具体名字不是兼容条件；错误 sequence 引用或 `nextval(...) + 1` 等行为变化被拒绝。连接角色的运行权限仍需部署配置保证。

required 列的 nullability 必须一致：更宽松会接纳应用不能处理的数据，更严格会拒绝应用合法写入。默认值通过 `pg_get_expr` 与有限的内置表达式 allowlist 比较：`now()`、`CURRENT_TIMESTAMP`、`transaction_timestamp()` 可作为同一事务时间语义；字符串/数值/boolean/JSONB 值按类型和值比较，不能简单删除空白后接受任意 SQL 表达式。

### 5.2 Primary key 与 unique

19 张表均要求有效 PK：除以下两表为复合 PK，其余全部是 `(id)`。

- `user_roles(user_id, role_id)`。
- `workspace_members(workspace_id, user_id)`。

必须存在下面的唯一语义；约束/索引名字可以不同，键列、表达式、predicate、有效性不能不同。

| 表 | 必需 unique keys |
| --- | --- |
| users | `(username)` |
| roles | `(name)` |
| files | `(object_key)` |
| run_artifacts | `(object_key)` |
| knowledge_bases | `(workspace_id, name)`；`(workspace_id, namespace)` |
| knowledge_index_versions | `(knowledge_base_id, version)` |
| run_events | `(run_id, sequence)` |

这里的 unique 是覆盖所有行的有效、ready、非 partial、非 expression B-tree unique；不能用只覆盖 ready 行的 partial index 替代 KB namespace unique。PK/FK 的列序也进入 fingerprint。历史 unique 的 nullable 列保持 PostgreSQL 默认 NULL 语义；新的 `NULLS NOT DISTINCT` 不是未经声明的等价替代。

不得要求历史不存在的 `review_items(run_id, field_id)`、document/file、KB current ownership 或 fill version FK，借此把正常 legacy 判为不兼容。这些约束如有需要，应在数据一致性校验后通过 forward migration 增加。

### 5.3 Foreign key

下表的引用目标均为对应表的 `(id)`。所有历史 FK 都是 `MATCH SIMPLE`、`ON UPDATE NO ACTION`、non-deferrable、已 validated。删除动作缩写 `C`=CASCADE，`S`=SET NULL，`R`=RESTRICT，`N`=NO ACTION（未写动作的默认值）。

| 来源表 | 必需 FK（列 → 表，删除动作） |
| --- | --- |
| user_roles | `user_id→users C`；`role_id→roles C` |
| refresh_tokens | `user_id→users C` |
| workspaces | `created_by→users N` |
| workspace_members | `workspace_id→workspaces C`；`user_id→users C` |
| files | `workspace_id→workspaces C`；`created_by→users N` |
| run_artifacts | `workspace_id→workspaces C`；`created_by→users N` |
| jobs | `workspace_id→workspaces C`；`created_by→users N` |
| run_events | `workspace_id→workspaces C`；`job_id→jobs S` |
| form_files | `workspace_id→workspaces C`；`file_id→files R`；`created_by→users N` |
| fill_runs | `workspace_id→workspaces C`；`form_file_id→form_files R`；`job_id→jobs S`；`filled_form_artifact_id→run_artifacts S`；`created_by→users N` |
| knowledge_bases | `workspace_id→workspaces C`；`created_by→users N` |
| knowledge_documents | `knowledge_base_id→knowledge_bases C`；`workspace_id→workspaces C`；`file_id→files R`；`created_by→users N` |
| knowledge_index_versions | `knowledge_base_id→knowledge_bases C`；`workspace_id→workspaces C`；`created_by→users N` |
| ingestion_jobs | `workspace_id→workspaces C`；`knowledge_base_id→knowledge_bases C`；`index_version_id→knowledge_index_versions S`；`job_id→jobs S`；`created_by→users N` |
| review_items | `workspace_id→workspaces C`；`run_id→fill_runs C`；`reviewer_id→users N` |

marker、users、roles、audit_logs 无 FK。`audit_logs.workspace_id/user_id`、`run_artifacts.run_id`、`run_events.run_id`、`jobs.resource_id`、`fill_runs.knowledge_base_id/index_version_id`、`knowledge_bases.current_index_version_id` 在 baseline 没有 FK；这些是具体缺口，不能将其意外当成已经被数据库保护的关系。

### 5.4 Catalog 反射与允许的附加对象

结构检查使用 `pg_namespace/pg_class/pg_attribute/pg_type/pg_attrdef/pg_constraint/pg_index/pg_depend/pg_sequence/pg_extension`，按 schema+table OID 联接；`information_schema` 可辅助报告，但不能靠 column_name 存在检查替代完整约束检查。

- required 表必须是永久普通表；同名 view、foreign table、partitioned table、临时表不自动采用。
- required 列必须存在且未 dropped；列物理顺序不作要求，比较列名、内置类型 OID/typmod、nullability、default。
- PK/unique 的底层索引须 valid、ready，FK 须 validated，并检查列、目标 schema/table、动作、match、deferrability。
- 检查 `uuid-ossp` 扩展及所需 UUID routines 属于受信任 extension；同名自定义函数不能替代。fresh 创建扩展失败就回滚并拒绝启动。
- 历史表不带应用 CHECK、RLS policy、非内部 trigger 或 rule。未经 profile 声明的额外写入限制/副作用应拒绝自动采用，而不是把它们藏在 schema fingerprint 之外。
- 允许不影响合法写入的新增 nullable 列或具有兼容 default 的列；额外 NOT NULL 且无 default 的列会使既有 INSERT 失败，应拒绝。未知 CHECK/unique/FK/generated 行为需要明确兼容 profile。
- 普通非 unique 性能索引可多可少，缺失时给诊断；不把性能索引名字当作 seed 执行历史。补建放在审阅过的 forward migration，不能在 adoption 前回放历史 SQL。

fingerprint 对反射报告按 schema/table/column/constraint 的稳定键排序后编码 SHA-256；不包含 OID、owner、创建时间或约束名字等跨数据库变化值，但包含约束语义。比较应输出可定位的稳定诊断，例如 `MIG_LEGACY_COLUMN_TYPE`、`MIG_LEGACY_NULLABILITY`、`MIG_LEGACY_DEFAULT`、`MIG_LEGACY_PK`、`MIG_LEGACY_FK`、`MIG_LEGACY_UNIQUE`、`MIG_HISTORY_CHECKSUM`、`MIG_PARTIAL_SCHEMA`。

## 6. 已实现的 forward migration

账本/catalog/adoption 的真实 PG 测试先通过，随后才新增 `000013_versioned_knowledge_publication.sql`。历史 1–12 的字节保持不变。

| 对象 | 实际字段与行为 |
| --- | --- |
| `knowledge_bases` | `activation_revision`、`source_revision`（bigint，非负，默认 0）、`source_dirty`（boolean，默认 false） |
| `knowledge_index_versions` | `storage_contract`、`validation_state`、`input_snapshot_json`、`input_snapshot_canonical`、`input_snapshot_hash`、`source_revision`、`expected_active_version_id`、`expected_activation_revision`、`validation_receipt_json`、`publication_state` |
| `knowledge_version_source_pins` | 每个 version/document 的 file UUID、workspace/KB owner、filename、relative_path、object_key、sha256、size_bytes、document_role；文件 FK 为 RESTRICT |
| `fill_runs` | 可空 `target_scope_json`、`global_scope_json`、双方 activation revision；旧 run 不伪造 scope |
| `fill_run_index_pins` | `(run_id,role)` 主键，role 为 target/global，固定 KB/version owner 和 revision |
| `fill_run_template_pins` | run 主键，固定 template 文件 UUID、object key、hash、size、filename；文件 FK 为 RESTRICT |

KB current 通过 `(current_index_version_id,id,workspace_id)` 复合 FK 引用 version 的 `(id,knowledge_base_id,workspace_id)`，阻止跨 KB/workspace 或悬空指针。版本输入/物理 scope/expected activation 和三种 pins 的 UPDATE 被不可变触发器拒绝；已非 NULL 的 fill 双 scope 同样不能被 UPDATE 改写。

legacy ready 版本保留 current，以 `legacy_unversioned + legacy_declared_ready` 明确标识。它们没有新 snapshot/receipt，也不被冒称为新验证成功。新 candidate 使用 `versioned_v1 + pending`，成功完成 count/hash/smoke protocol 后才成为 `validated + ready`。forward migration 不验证旧 Qdrant 数据是否存在。

输入 canonical JSON 原字节与 hash 同时存储；`input_snapshot_json` 仅用于结构化查询。私有 `input_dir` 位于版本 UUID 目录，snapshot 内 relative path 为 `documentUUID/fileUUID/sanitizedFilename`，重复 filename 不碰撞。源文件按 UUID 排序 `FOR UPDATE`，pin 与文件删除共用这些锁。

## 7. 实际生产事务 API

实现位于 `knowledge/build_store.go`，使用 `TxManager.WithTx`，由已有 admin/workspace 授权后的 service 调用。公共构造为 `NewPGXBuildStore(pool)`；两种 knowledge service 及 ingestion lifecycle 提供 `SetBuildStore`。

| 入口 | 已实现的事务保证 |
| --- | --- |
| `CreateBuild(ctx, CreateBuildRequest) (*BuildCreation,error)` | KB 行锁下分配 `MAX(version)+1`，冻结 active UUID/revision 和 source revision；candidate、canonical snapshot、source pins、ingestion、job 及 association 一起提交 |
| `MarkRunning(ctx, ingestionID)` | 锁 KB/version/ingestion；已 validated completion 幂等；历史缺 snapshot 的 pending build 拒绝恢复，提示新建 build；重试先核对并锁住冻结 source files |
| `CompleteBuild(ctx, ingestionID, result)` | 校验冻结 snapshot/hash、typed receipt、job 取消状态；候选 ready/receipt、CAS current/revision、ingestion success 和 pinned document 状态一起提交或回滚 |
| `ActivateVersion(ctx, kbID, workspaceID, versionID, expectedID, revision)` | 验证 owner/serving scope，CAS 前一 UUID 和 revision；显式切换/回滚每次 revision+1；不归档旧 ready |
| `FailBuild(ctx, ingestionID, message, canceled)` | 只使未 validated candidate 与该 ingestion 失败/取消；保留 current；已完成版本不降级 |
| `CancelBuild(ctx, ingestionID, actorID)` | 同 publication 的 KB→version/ingestion→job 锁序；验证 job owner；published 拒绝 late cancel；created/queued 同事务 job/domain canceled，running 同事务双方 cancel_requested |
| `CancelWorkerJob(ctx, jobID, actorID)` | generic jobs cancel hook：映射 ingestion 后复用同一 cancel 事务，并在锁内复核 association；无关联 legacy job 明确返回 nil,nil，允许原 jobs cancel fallback |
| `ReadPublishedIngestion(ctx, ingestionID)` | 从持久化 succeeded、validated、activated/superseded 状态恢复 receipt 与 artifact 路径，独立于当前指针；后续用户回滚不会触发已完成数据重新构建 |
| `ResolveCurrentScopeTx(ctx,tx,kbID,workspaceID)` | caller 已锁 KB；验证 owner/namespace/collection/ready+storage/validation，拒绝 archived/空白/Nil scope；source dirty、building/failed candidate 不阻止可用 current |
| `CreateFillRunPinned` | 按 UUID 排序锁 target/global KB，固定双 scope/template；fill run、pins、job association 一起提交；不同 collections 明确拒绝 |

单 KB 锁序为 KB→version→ingestion/job→files/pins；多 KB 按 UUID 排序。源上传/注册/删除与 source_revision/dirty 的更新在 PG 同一事务中，已有 candidate 输入不会改变。

### 7.1 Dispatch 与事件边界

本阶段没有新通用 outbox 表。job row 是 durable pending dispatch：事务内仅插入，commit 后调用 `EnqueuePersistedJob`；Redis 失败保留可恢复 row，service 返回已提交领域记录；恢复扫描 created/queued jobs。Redis 使用稳定 job TaskID 抑制重复 enqueue。

publication commit 后 worker 才发 ready 事件。没有 event outbox，所以 PG commit 与事件发布之间仍有通知遗漏窗口；持久化状态和 publication recovery 是权威结果，不能宣称 exactly-once event delivery。

### 7.2 Validation receipt 与 CAS

协议由 `knowledge/protocol.go` 冻结。`kb-index-validation-v1` receipt 的字段为：schema_version、index_version_id、knowledge_base_id、namespace、collection、input_snapshot_hash、expected/actual evidence count、expected/actual schema count、document_count、source_hashes_verified、smoke_passed、validated_at。没有独立 artifacts-hash 或 publication UUID 字段。Go 检查 UUID/scope/hash 严格相等，evidence count 非空且相等，schema count 非负且相等，document_count 与冻结清单相等，source hashes 与 smoke 均 true，时间非零。

Python 在候选 UUID 下完成索引与真实验证，PG current commit 是发布点。CAS 同时比较 expected active UUID 和 activation_revision，防止 V1→V2→V1 的 ABA。并发 A/B 由一个成功激活；另一个保留 validated ready 并标记 superseded，后续可显式激活。重复 completion 返回成功，不再次移动 current。

取消 job 已写入 cancel_requested 但 ingestion 尚未更新时，publication 同事务检查并锁住 job，拒绝发布。typed cancel 通过 `CancelBuild` 与 publication 串行决策，已 committed publication 不接受随后取消。历史缺 canonical snapshot 的 ingestion 失败只改变该 ingestion，不降级它可能引用的 legacy current。失败/cancel/interrupted recovery 不降级已完成版本。生命周期数据库错误向 worker 传播。

### 7.3 Serving 与冻结 fill scope

ready options 和普通用户 KB 可见性由 effective current 决定。V2 failed/canceled、source dirty 不隐藏可用 V1。明确 archived KB 不可选择。显式 legacy scope 固定 KB/namespace/collection，新 scope 还包含 version UUID；worker/Python 不在排队后重新读取 current。

本阶段 target/global 必须同 collection；不同 collection 请求返回明确 conflict。manifest/input snapshot/job payload/CLI/Qdrant filter 使用冻结双 scope，不声称已经实现跨 collection 检索。

### 7.4 保留与后续 GC

本阶段不提供 `ClaimVersionGC`、`CompleteVersionGC` 或定时删除。旧 ready 版本、旧物理 points 与 source pins 保留，支持显式回滚和 queued run 的旧 scope。文件软删除检查所有未释放的 source pins 与 fill template pins，包括失败候选的 source pins；legacy current/fill-pinned 版本通过关联 documents 保留源文件，不伪造 legacy snapshot。

精确版本 GC、claim 状态和 retention 删除属于后续设计，尚未实施。任何候选失败清理都不能扩大到 namespace 或删除 legacy/current 数据。

## 8. 真实 PostgreSQL 验证与限制

隔离环境为 `datacenter-vnext-pg`，PostgreSQL 16.4/arm64、127.0.0.1:55432，tmpfs PGDATA，无原 Compose volume。环境证据见 `artifacts/vnext/environment/README.md`。测试在随机独立数据库中运行，结束 DROP DATABASE，不修改 `vnext_test` 或原项目数据库。

| 已实现测试文件 | 真实 PG 断言 |
| --- | --- |
| `internal/database/migrate_postgres_test.go` | fresh/concurrent boot 与 restart不重放；legacy V2及明确V1 rollback、软删保留；adopted provenance和13 forward；type/default/nullability/owned sequence/FK/unique/view/RLS/trigger/check拒绝；历史 checksum、partial ledger与事务全批次回滚 |
| `internal/knowledge/build_postgres_test.go` | 并发 ordinal与publication CAS；creation job association失败完整回滚；receipt负例；publication各SQL步骤故障回滚；rollback/ABA/idempotent recovery；取消前后顺序及并发单一决策/job owner；source revision及重复filename、dirty仍服务；legacy缺snapshot拒绝；immutable inputs/pins |
| `tests/pinned_fill_postgres_test.go` | target/global分别pin；排队后current切换保留scope/payload；template/source删除保护与并发锁；owner/namespace/collection/validation/archived fail-closed |

这些 PG publication 测试的 receipt 是明确的协议 fixture，不调用模型，也不证明 Qdrant 中真实点数或 smoke 成功；真实 Python/Qdrant验证及整条E2E需独立验收。

普通 Go unit target 可以在无 DSN 时跳过 PG 测试。`make test-postgres` 要求 `VNEXT_TEST_POSTGRES_DSN` 非空并以 race detector 运行 Postgres测试；缺DSN立即失败。`.github/workflows/go-server-ci.yml` 提供 PostgreSQL 16.4 service并强制执行该target，不能以全skip作为数据库验收。

## 9. 部署约束

停止旧 API/worker 写入节点后再替换为 ledger-aware binary；禁止旧binary混合滚动或自动重新启动，因为它不识别账本且仍会重放历史 seeds。新engine的advisory lock只协调新迁移进程。数据回滚通过明确version激活接口完成，历史Down不自动执行。

当前迁移不能修复过去已经被重放覆盖的用户current选择；需要从已有审计/备份恢复。部分或畸形legacy结构拒绝采用，不通过重播历史SQL自动补齐。没有任何模型请求、原项目数据库迁移或外部Qdrant数据变更由上述PG测试执行。
