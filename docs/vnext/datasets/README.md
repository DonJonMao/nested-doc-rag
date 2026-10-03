# Phase 5 固定匿名合成数据

本目录提交数据集声明和独立金标，知识文件、待填表单和实际解析字段由 [生成器](../../../scripts/vnext_generate_datasets.py) 产生在 ignored `artifacts/vnext/phase5/datasets/`。三对数据没有使用原机房资料、旧 141 字段、历史 `form_items.jsonl`、seed 文件或模型运行产物。每对必须创建自己的 workspace、target KB 和 global KB；三对复用 collection 名称，不复用 KB、scope、源文件或表单。

```sh
python scripts/vnext_generate_datasets.py
python scripts/vnext_generate_datasets.py --check --output-root artifacts/vnext/phase5/datasets-reproduction
```

生成器只使用生产已有的 `openpyxl`、`python-docx` 和原生 source/form parser，不调用模型、索引或检索。ZIP 条目时间和 OOXML 创建/修改时间固定。`manifest.json` 保存当前文件 SHA256 和生成库版本；`--check` 在另一个目录重建并比较全部知识/表单/字段文件 hash 与 gold bytes，声明文件保持不动。库版本变化造成 hash 差异时必须审阅，不能默默覆盖固定声明。执行证据为 binary 根目录的 `validation.json`。

| 对 | 新知识结构 | 新表单结构 | 实际字段 |
| --- | --- | --- | --- |
| F1 北辰 | 两个机房 Excel label/value、独立设备明细、Word 电池实测及独立 global 标准 | 新文件名，E 列填写，从第 7 行开始 | 10 |
| F2 杉岚 | Word 原生段落和两张设备表、一个确认事实 Excel 及独立 global 标准 | 两个 sheet，列重新排序，C/F 两种填写列，合并类别 | 10 |
| F3 澄海 | Excel 现网/规划/异房/设备台账、Word 模块巡检及独立 global 标准 | B/D 两种填写列，第二 sheet 第 146–150 行 | 10 |

F3 的 C28 目标原来包含人工值 `人工确认1200kg`：答案金标为 `1200kg`，默认 preserve 必须保留原值，动作是 `skipped_non_empty_cell`。额外 `改造核查!D151` 的 `=SUM(1,2)` 是公式保护控制，parser 必须报告 `formula_target` 且输出继续保留公式；它不计入 30 条挑战，也不计为运行过的回答字段。

`manifest.pairs` 接口为 `id/room_context/namespaces/collection/target_sources/global_sources/template/form_items/gold`。每个 `path` 相对 manifest 所在目录；例如 `../../../artifacts/vnext/phase5/datasets/f1/target/北辰设施.xlsx`。`template.expected_fields=10` 是实际 parser 数量，`expected_targets` 是物理写回坐标。`form_items` 是本次新表单的原生解析结果，默认不含 heldout。Docker E2E 必须上传模板并让生产链路重新解析；消融的统一输入可以读取这些显式字段，不能据此省略生产上传验收。

Gold 每行是一个稳定 `case_id`，按 `dataset_id` 选择本对的 10 行。`field_id` 来自当前原生表单身份；答案、预期状态、来源原文和地址在生成器 recipe 中先声明，parser 只校验物理一致性。金标不用运行中引用 ID、打分或模型答案反推。可回答项的 raw `expected_answer_status=answered`，写回状态另列为 `expected_writeback_status=confirmed`；C30 为 `partial_clue`、`未找到`，不得写回。

每条 `required_evidence` / `decoy_evidence` 包含文件键/名、namespace role、机房范围和真实 Excel sheet/cell range 或 Word paragraph/table row。Excel 和 Word 原生索引均从 1 开始。`source_text` 是完整原生行/段落，`source_text_hash` 是该完整文本的 SHA256，`source_text_contains=false`，不把截取 quotation 的 hash 当作完整来源 hash。不同 required 条目具有同一 `fact_id` 时是可替代来源；不同 `fact_id` 必须全部支持。运行时 UUID/path 前缀由 ingestion ledger 映射，物理地址和已声明文本保持原义。

规则先固定：区分字段名称与数值，尊重问题明确指定的机房，当前与规划分别回答，单位与单台/总量不能混用；企业默认标准不能替代目标机房已投运事实。复合问题要求所有事实：C10、C20、C29 必须补足原文事实，C30 的柴油储量在整套资料中缺失，始终拒绝 confirmed。`sufficiency_expected` 是语义验收预期，原生 parser 校验本身不证明真实模型达成了它。C11/C14/C16/C18 等仅有段落证据的字段也可能需要第二轮，不能只将主分类 `second_round` 当作触发率分母。

| Case | 主挑战 | 答案金标 | 独立来源地址 |
| --- | --- | --- | --- |
| C01 | 同值不同字段 | 2路 | 北辰设施 / 北辰301 A2:B2 |
| C02 | 同值不同字段 | 2路 | 北辰设施 / 北辰301 A3:B3 |
| C03 | 同字段不同机房 | 500kVA | 北辰设施 / 北辰301 A4:B4 |
| C04 | 同字段不同机房 | 150㎡ | 北辰设施 / 北辰301 A8:B8 |
| C05 | 现状与规划 | 24架 | 北辰设施 / 北辰301 A6:B6 |
| C06 | 数字干扰 | 600kW | 北辰设施 / 北辰301 A12:B12 |
| C07 | 数字子串 | 150㎡ | 北辰设施 / 北辰301 A8:B8 |
| C08 | target/global 冲突 | 500kVA | 北辰设施 / 北辰301 A4:B4 |
| C09 | table detail | DG-600A | 北辰设施 / 设备明细 A2:D2 |
| C10 | 第二轮补足 | 2小时；4组 | 北辰设施 / 北辰301 A11:B11 ＋ 北辰电池实测 paragraph 3 |
| C11 | 同值不同字段 | 2个 | 杉岚运行资料 paragraph 3 |
| C12 | 同值不同字段 | 3套 | 杉岚确认清单 / 杉岚201确认 A2:B2 或 杉岚运行资料 paragraph 5 |
| C13 | 同字段不同机房 | 180kW | 杉岚运行资料 table 1 row 2 |
| C14 | 同字段不同机房 | 60分钟 | 杉岚运行资料 paragraph 11 |
| C15 | 现状与规划 | 3台 | 杉岚运行资料 paragraph 6 或 table 1 row 2 |
| C16 | 现状与规划 | 1.42 | 杉岚运行资料 paragraph 9 |
| C17 | 数字子串 | 180kW | 杉岚运行资料 table 1 row 2 |
| C18 | target/global 冲突 | 七氟丙烷 | 杉岚运行资料 paragraph 13 |
| C19 | table detail | 岚峰 | 杉岚运行资料 table 1 row 2 |
| C20 | 第二轮补足 | 2路；杉岚东变电站和杉岚西变电站 | 杉岚运行资料 table 2 row 2 ＋ paragraph 14 |
| C21 | 同值不同字段 | 4台 | 澄海现网 / 南501 A2:B2 |
| C22 | 同字段不同机房 | 8kW | 澄海现网 / 南501 A4:B4 |
| C23 | 现状与规划 | 10Gbps | 澄海现网 / 南501 A7:B7 |
| C24 | 数字子串 | 500kVA | 澄海现网 / 南501 A9:B9 |
| C25 | target/global 冲突 | 3路 | 澄海现网 / 南501 A5:B5 |
| C26 | target/global 冲突 | 30分钟 | 澄海现网 / 南501 A6:B6 |
| C27 | table detail | QF-160 | 澄海现网 / 设备台账 A2:D2 |
| C28 | table detail / 人工值保护 | 1200kg，保留原人工值 | 澄海现网 / 设备台账 A3:D3 |
| C29 | 第二轮补足 | N+1；3个 | 澄海现网 / 南501 A11:B11 ＋ 澄海UPS巡检 paragraph 3 |
| C30 | 第二轮仍缺口 | partial_clue / 未找到 / 禁止写回 | 澄海现网 / 南501 A12:B12 只有已知容量，柴油储量无来源 |

主分类数量为 5 同值、5 异房、4 现状规划、4 数字、4 target/global、4 table detail、4 补查，合计 30。可回答 29 项；默认策略可写回 28 项，C28 保留人工值，C30 拒绝写回。Decoy 的独立地址分别标注 `wrong_field/wrong_scope/planning/global_conflict/substring`；错误率必须依据这些明确金标而非系统自己的引用完整性计算。

这些素材是人为构造的合成挑战，能够支持确定性契约和受控模型比较，不能据此宣称真实机房泛化。生成与源地址校验自身没有外部模型请求或 Docker 执行。后续三对 Docker upload→fill→download 工程链路已经使用显式 stub 通过，独立 ledger 记录 runtime、模型类型、实际 runner、初始空 storage、commit/config/dataset hash 及成功/失败产物，见 [PHASE5.md](../PHASE5.md)。显式 stub 结果不能冒称真模型验收。
