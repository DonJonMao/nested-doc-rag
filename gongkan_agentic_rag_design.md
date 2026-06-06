# 工勘单自动填写项目设计文档

## 项目目标

本项目目标是：针对嵌套型、格式不统一、来源复杂的工勘资料，构建一个能够自动阅读知识库文档、理解甲方工勘单、检索答案证据、按样例格式填写工勘单的系统。

整体流程分为四部分：

1. 文档的切分和打标  
2. 向量化  
3. 工勘单阅读  
4. 工勘单填写  

整个系统不做预训练或微调作为主路线，而是采用：

```text
层级文档阅读
→ 逻辑切分
→ 父子标签传递
→ 向量化入库
→ 工勘单字段理解
→ 自上而下回溯检索
→ 按样例格式生成答案
→ 证据校验与回写
```

核心原则：

```text
不按固定长度切分；
不把 Excel 粗暴转成纯文本；
不让模型直接整表生成；
不允许无证据填写；
每个答案必须能回溯到原始文档位置。
```

## 当前数据集适配结论

当前 `/data` 目录的主要资料形态是：

```text
知识库：以 Excel 能力清单为主，少量 Word 情况说明为辅；
工勘单：以 Excel 表单为主；
证明材料：大量以 WPS DISPIMG 图片或嵌入媒体形式挂在 Excel 证明材料列；
数据中心范围：西咸 1-6 号楼、西安、城东/灞桥、咸阳，共 9 个目标库。
```

因此第一版不应从“全类型复杂嵌套文档 Agentic RAG”直接做起，而应先落到更稳定的主链路：

```text
9 个数据中心分库
→ Excel 行级能力项解析
→ 图片作为附件证据打标
→ 行级 segment 标准化
→ 分库内检索
→ 工勘单字段级生成、校验、回写
```

第一版边界：

```text
图片不做 OCR，不进入语义检索主链路；
图片只作为文字证据命中后的附件佐证；
优先解析 Excel 能力清单的一行一项；
Word 情况说明作为补充知识源；
嵌套附件递归抽取作为后续增强能力，不作为第一版主路径。
```

数据中心分库建议：

| data_center_id | 名称 | 别名 |
|---|---|---|
| xixian_1 | 西咸数据中心 1 号楼 | 西咸1号楼 |
| xixian_2 | 西咸数据中心 2 号楼 | 西咸2号楼 |
| xixian_3 | 西咸数据中心 3 号楼 | 西咸3号楼 |
| xixian_4 | 西咸数据中心 4 号楼 | 西咸4号楼 |
| xixian_5 | 西咸数据中心 5 号楼 | 西咸5号楼 |
| xixian_6 | 西咸数据中心 6 号楼 | 西咸6号楼 |
| xian | 中国移动（陕西西安）数据中心 | 锦业路、西安 |
| chengdong_baqiao | 城东数据中心 | 灞桥、城东、港务区 |
| xianyang | 中国移动（陕西咸阳）数据中心 | 咸阳 |

---

# 一、文档的切分和打标

## 1. 从最上层文档向下阅读

### 1.1 原始文件登记

首先对用户上传或指定的知识库文件做文件登记。

输入：

```text
docx
xlsx
xls
pdf
图片
压缩包
嵌套附件
```

输出：

```json
{
  "file_id": "file_001",
  "file_name": "数据中心工勘资料.xlsx",
  "file_type": "xlsx",
  "data_center_id": "xixian_2",
  "data_center_name": "西咸数据中心2号楼",
  "document_role": "knowledge_base",
  "parent_file_id": null,
  "level": 0,
  "source_path": "/raw/数据中心工勘资料.xlsx"
}
```

这一阶段只做文件级记录，不做语义理解。

回测指标：

```text
文件登记完整率 = 已登记文件数 / 实际文件数
文件类型识别准确率
文件层级识别准确率
```

验收标准：

```text
所有顶层文件必须被登记；
文件名、类型、路径不能丢；
顶层文件 level = 0；
当前数据集必须识别 data_center_id；
必须识别 document_role：knowledge_base、survey_form、intro_doc、proof_attachment。
```

---

### 1.2 递归解包嵌套文档

很多知识库文件是：

```text
Word 套 Word
Word 套 Excel
Excel 套 Word
Excel 套 Excel
Excel 中嵌入附件对象
Excel 中插入图片
```

需要从顶层文档开始，递归抽取下层嵌套文件。

当前数据集的落地要求：

```text
第一版只要求登记嵌入对象和证明材料，不要求完整解析所有嵌套附件；
Excel 中的图片证明材料不视为独立知识文档，而是挂到同行文字 segment 的 proof_attachments；
Word / Excel 中真正嵌入的 docx、xlsx、pptx 可以先保存文件级 metadata，后续再进入递归解析；
如果文件扩展名与内部结构不一致，必须标记 parse_status，不允许静默失败。
```

输出：

```json
{
  "file_id": "file_003",
  "file_name": "附件2_设备清单.docx",
  "file_type": "docx",
  "parent_file_id": "file_001",
  "ancestor_file_ids": ["file_001"],
  "level": 1,
  "lineage_path": [
    "数据中心工勘资料.xlsx",
    "Sheet: 附件目录",
    "Object: 附件2_设备清单.docx"
  ]
}
```

回测指标：

```text
嵌套文件召回率 = 成功抽取的嵌套文件数 / 实际嵌套文件数
父子关系准确率
lineage_path 准确率
```

验收标准：

```text
能够知道每个下层文件来自哪个父文档；
能够知道它在父文档中的位置；
嵌套层级不能断。
```

---

## 2. 结构解析、智能体判读与切分

这一部分是整个项目的核心。先用程序做可复现的结构解析，再让智能体解释结构和语义，最后按内容逻辑切分。

边界：

```text
4A 确定性结构解析：程序读取事实、坐标、公式、图片关系；
4B 智能体结构判读：模型判断 sheet 类型、表头、列语义、切分策略；
后续切分与打标：根据 4B 输出生成候选 segment。
```

### 2.1 4A：确定性结构解析

4A 是“程序读取事实和坐标”的阶段，不使用大模型，不做语义判断，不生成答案。

4A 的目标：

```text
把原始 docx / xlsx 文件解析成可复现、可回溯的结构化中间结果；
保留每个文本值、公式、图片、合并单元格、隐藏行列、样式和附件的原始位置；
为后续 4B 智能体结构判读提供稳定输入；
发现异常文件时显式输出 parse_status 和 fallback_action。
```

4A 的非目标：

```text
不判断 sheet 是能力清单还是工勘单；
不判断哪一列是问题列、答案列、证明材料列；
不做图片 OCR；
不让图片单独成为知识；
不做行级 segment 生成；
不做语义摘要、关键词、实体抽取。
```

运行环境：

```text
conda 环境：datacenter
Python：3.11.x
执行方式：所有解析、回测、CLI 都通过 conda run -n datacenter 执行
```

建议依赖：

```bash
conda run -n datacenter python -m pip install openpyxl lxml python-docx pillow olefile xlrd
```

注意：

```text
pandas 不作为 4A 的核心解析器；
pandas 容易丢失单元格坐标、公式、合并单元格、图片关系；
4A 应优先使用 zipfile + XML 解析 OOXML 包结构。
```

---

#### 2.1.1 4A 输入

4A 输入来自前置文件登记和数据中心路由，不直接扫未知目录。

输入记录：

```json
{
  "file_id": "file_xixian_2_kb",
  "source_path": "/Users/mao/projects/datacenter/data/西咸数据中心2号楼维护能力知识库.xlsx",
  "file_name": "西咸数据中心2号楼维护能力知识库.xlsx",
  "declared_ext": ".xlsx",
  "document_role": "knowledge_base",
  "data_center_id": "xixian_2"
}
```

4A 可以接受的文件角色：

```text
knowledge_base
survey_form
intro_doc
unknown
```

4A 不根据角色改变事实解析规则，只把角色写入输出 metadata。

---

#### 2.1.2 4A 输出目录

建议把 4A 输出放到独立工件目录，避免污染原始文件。

目录结构：

```text
/Users/mao/projects/datacenter/artifacts/4a_structure/
  files.jsonl
  parse_report.json
  workbooks/
    {file_id}.workbook.json
  worksheets/
    {file_id}.{sheet_index}.cells.jsonl
    {file_id}.{sheet_index}.sheet.json
  documents/
    {file_id}.docx.json
  attachments/
    {file_id}.attachments.jsonl
  media/
    {file_id}/...
  diagnostics/
    {file_id}.diagnostics.json
```

输出原则：

```text
大体量单元格用 JSONL；
文件级、sheet 级摘要用 JSON；
图片媒体可先不复制，只记录 package 内路径；
需要导出审计附件时再复制 media。
```

---

#### 2.1.3 4A 模块设计

建议代码结构：

```text
src/gongkan_rag/
  structure/
    cli.py
    file_probe.py
    ooxml_package.py
    xlsx_parser.py
    xlsx_values.py
    xlsx_styles.py
    xlsx_images.py
    docx_parser.py
    schemas.py
    diagnostics.py
```

模块职责：

| 模块 | 职责 |
|---|---|
| `cli.py` | 提供命令行入口，批量解析文件清单 |
| `file_probe.py` | 判断真实文件类型、扩展名是否匹配、是否加密或损坏 |
| `ooxml_package.py` | 读取 zip 包、关系文件、Content Types |
| `xlsx_parser.py` | 解析 workbook、sheet、row、cell、merge、hidden 信息 |
| `xlsx_values.py` | 解析 sharedStrings、inlineStr、公式、缓存值 |
| `xlsx_styles.py` | 解析字体、填充色、边框、数字格式、标红/必填样式 |
| `xlsx_images.py` | 解析 WPS DISPIMG、cellimages、drawing anchors、media 映射 |
| `docx_parser.py` | 解析 Word 段落、标题、表格、图片、嵌入对象 |
| `schemas.py` | 定义结构化输出 schema |
| `diagnostics.py` | 生成异常、质量检查和回测统计 |

CLI 示例：

```bash
conda run -n datacenter python -m gongkan_rag.structure.cli \
  parse \
  --manifest /Users/mao/projects/datacenter/artifacts/file_manifest.jsonl \
  --out /Users/mao/projects/datacenter/artifacts/4a_structure
```

---

#### 2.1.4 文件探测与解析策略选择

4A 第一件事不是按扩展名解析，而是探测真实格式。

探测项：

```text
文件是否存在；
文件大小；
声明扩展名；
文件头 magic；
是否 zip；
是否包含 [Content_Types].xml；
是否包含 xl/workbook.xml；
是否包含 word/document.xml；
是否 OLE Compound Document；
是否混合/异常包；
是否加密；
是否能读取中央目录；
是否存在 WPS cellimages。
```

输出：

```json
{
  "file_id": "file_xixian_6_kb",
  "declared_ext": ".xlsx",
  "detected_package_type": "mixed_or_invalid_xlsx",
  "parse_status": "needs_conversion",
  "fallback_action": "open_and_resave_with_wps_or_libreoffice",
  "diagnostics": [
    "zip central directory readable",
    "xl/workbook.xml missing",
    "xl/cellImages.xml exists",
    "extra bytes before zip payload"
  ]
}
```

解析策略：

| 条件 | parser_type | parse_status |
|---|---|---|
| zip + `xl/workbook.xml` | `xlsx_ooxml` | `ok` |
| zip + `word/document.xml` | `docx_ooxml` | `ok` |
| zip 但缺 workbook/document 主体 | `package_probe_only` | `needs_conversion` |
| OLE Compound Document | `ole_probe` | `needs_conversion` |
| 文件损坏 | `none` | `corrupt` |
| 加密文件 | `none` | `encrypted` |

验收标准：

```text
任何文件都必须输出 file_probe 结果；
任何解析失败都必须有 parse_status 和 fallback_action；
不能因为解析失败而跳过文件。
```

---

#### 2.1.5 Excel OOXML 解析流程

标准 `.xlsx` 按 OOXML 包结构解析。

读取顺序：

```text
1. [Content_Types].xml
2. _rels/.rels
3. xl/workbook.xml
4. xl/_rels/workbook.xml.rels
5. xl/sharedStrings.xml
6. xl/styles.xml
7. xl/worksheets/sheet*.xml
8. xl/worksheets/_rels/sheet*.xml.rels
9. xl/drawings/*.xml
10. xl/drawings/_rels/*.rels
11. xl/cellimages.xml 或 xl/cellImages.xml
12. xl/_rels/cellimages.xml.rels 或 xl/_rels/cellImages.xml.rels
13. xl/media/*
14. xl/embeddings/*
```

关键规则：

```text
以 worksheet XML 中真实 c/@r 作为单元格坐标；
不要只信 worksheet dimension；
不要只信 openpyxl max_row / max_column；
空单元格不生成 CellRecord，但 sheet 级统计要记录空白边界；
公式同时保存 formula_text 和 cached_value；
DISPIMG 公式保存原始公式和 image_id；
合并单元格只输出 merge ranges 和 master cell，不在 4A 做语义类别传播；
隐藏行列、样式、批注、超链接都作为结构属性输出。
```

单元格记录：

```json
{
  "file_id": "file_xixian_2_kb",
  "sheet_index": 1,
  "sheet_name": "西咸数据中心2号楼",
  "cell_ref": "E54",
  "row": 54,
  "col": 5,
  "raw_type": "formula",
  "value": null,
  "formula_text": "_xlfn.DISPIMG(\"ID_xxx\",1)",
  "formula_kind": "wps_dispimg",
  "style_id": 12,
  "is_hidden_row": false,
  "is_hidden_col": false,
  "merge_master": null,
  "source_anchor": {
    "file_name": "西咸数据中心2号楼维护能力知识库.xlsx",
    "sheet_name": "西咸数据中心2号楼",
    "cell": "E54"
  }
}
```

Sheet 摘要：

```json
{
  "file_id": "file_xixian_2_kb",
  "sheet_index": 1,
  "sheet_name": "西咸数据中心2号楼",
  "declared_dimension": "A1:H213",
  "actual_min_cell": "A1",
  "actual_max_cell": "H213",
  "non_empty_cell_count": 1096,
  "merge_count": 8,
  "formula_count": 175,
  "dispimg_formula_count": 167,
  "hidden_row_count": 0,
  "hidden_col_count": 0,
  "parse_status": "ok"
}
```

合并单元格记录：

```json
{
  "sheet_name": "西咸数据中心2号楼",
  "range": "B3:B77",
  "master_cell": "B3",
  "master_value": "现场环境",
  "covered_cells": ["B4", "B5", "B6"]
}
```

---

#### 2.1.6 图片与附件解析

图片只做结构打标，不做 OCR。

图片来源分三类：

```text
WPS DISPIMG 单元格图片；
标准 OOXML drawing 锚定图片；
嵌入对象中的附件文件。
```

WPS DISPIMG 解析流程：

```text
1. 从公式中提取 image_id，例如 ID_7C179F...
2. 解析 xl/cellimages.xml / xl/cellImages.xml；
3. 找到 cNvPr/@name 与 image_id 的匹配关系；
4. 读取 blip r:embed；
5. 解析 xl/_rels/cellimages.xml.rels；
6. 将 rId 映射到 xl/media/image*.png 或 jpeg；
7. 将图片绑定到公式所在 cell_ref。
```

附件记录：

```json
{
  "attachment_id": "att_file_xixian_2_E54_01",
  "file_id": "file_xixian_2_kb",
  "sheet_name": "西咸数据中心2号楼",
  "anchor_type": "cell_formula",
  "source_cell": "E54",
  "image_id": "ID_xxx",
  "relationship_id": "rId54",
  "media_path": "xl/media/image54.png",
  "media_content_type": "image/png",
  "attachment_type": "image",
  "ocr_status": "not_required",
  "used_for_generation": false,
  "used_for_audit": true
}
```

验收标准：

```text
DISPIMG 公式提取率 >= 98%；
image_id 到 media_path 映射准确率 >= 98%；
附件必须能回到原始 file、sheet、cell；
图片不能生成文本证据；
图片不能进入 embedding_text。
```

---

#### 2.1.7 Word DOCX 解析流程

Word 不是当前第一版主知识源，但 4A 要能生成基础结构。

读取顺序：

```text
1. word/document.xml
2. word/_rels/document.xml.rels
3. word/styles.xml
4. word/numbering.xml
5. word/header*.xml / footer*.xml
6. word/media/*
7. word/embeddings/*
```

输出对象：

```text
paragraph
heading
table
table_row
table_cell
image
embedded_object
header
footer
```

段落记录：

```json
{
  "file_id": "file_xian_intro_doc",
  "block_type": "paragraph",
  "block_index": 12,
  "text": "机房采用空调下送风方式，铺设防静电地板...",
  "style_name": "Normal",
  "source_anchor": {
    "file_name": "中国移动（陕西西安）数据中心机房情况说明介绍.docx",
    "block_index": 12
  }
}
```

表格单元格记录：

```json
{
  "file_id": "file_xian_intro_doc",
  "block_type": "table_cell",
  "table_index": 1,
  "row_index": 2,
  "col_index": 4,
  "text": "200G",
  "source_anchor": {
    "file_name": "中国移动（陕西西安）数据中心机房情况说明介绍.docx",
    "table_index": 1,
    "row_index": 2,
    "col_index": 4
  }
}
```

验收标准：

```text
段落顺序必须稳定；
表格行列坐标必须保留；
图片和嵌入对象只打标，不做 OCR；
Word 解析失败必须有 parse_status。
```

---

#### 2.1.8 4A 质量检查和回测

4A 每次运行都必须生成 `parse_report.json`。

文件级统计：

```json
{
  "total_files": 19,
  "parsed_ok": 18,
  "needs_conversion": 1,
  "corrupt": 0,
  "encrypted": 0
}
```

Excel 级统计：

```text
sheet_count
actual_cell_count
declared_dimension
actual_dimension
dimension_mismatch
merge_count
formula_count
dispimg_formula_count
attachment_count
attachment_mapping_rate
hidden_row_count
hidden_col_count
```

当前数据集必须覆盖的回测用例：

| 用例 | 期望 |
|---|---|
| 标准能力清单 Excel | 能输出 sheet、cell、merge、formula、attachment |
| `陕西西安移动三线CDN机房排查表（2025.07）.xlsx` | 不能被 `dimension=A1` 误判为空表，必须扫描真实 cell ref |
| WPS `DISPIMG` 文件 | 能提取 image_id 并映射 media_path |
| `西咸数据中心6号楼维护能力知识库.xlsx` | 必须标记 `needs_conversion`，不能静默失败 |
| Word 情况说明 | 能输出段落、表格、图片和 source_anchor |

4A 验收指标：

```text
文件探测覆盖率 = 100%；
标准 xlsx sheet 识别完整率 >= 99%；
真实 cell ref 解析准确率 >= 99%；
公式文本保留率 >= 99%；
合并单元格 range 解析准确率 >= 99%；
DISPIMG 图片 ID 提取准确率 >= 98%；
图片 media_path 映射准确率 >= 98%；
异常文件 parse_status 覆盖率 = 100%。
```

---

#### 2.1.9 4A 到 4B 的交接

4A 输出给智能体的不是原始 Excel，也不是整表文本，而是受控结构摘要。

4B 输入包：

```json
{
  "file_id": "file_xixian_2_kb",
  "data_center_id": "xixian_2",
  "sheet_name": "西咸数据中心2号楼",
  "sheet_summary": {
    "actual_dimension": "A1:H213",
    "non_empty_cell_count": 1096,
    "merge_count": 8,
    "formula_count": 175,
    "attachment_count": 167
  },
  "sample_rows": [
    ["序号", "类别", "能力描述", "是否满足（具体数值）", "证明材料"],
    ["1", "现场环境", "数据中心位置，名称，机房名称", "陕西省咸阳市...", "DISPIMG:ID_xxx"]
  ],
  "merge_ranges": [
    {"range": "B3:B77", "master_cell": "B3", "master_value": "现场环境"}
  ],
  "style_hints": {
    "red_cells": ["B2", "C2"],
    "bold_rows": [1, 2]
  }
}
```

4B 使用这些信息判断：

```text
sheet_type；
header_row；
column_mapping；
segment_strategy；
是否需要人工确认。
```

边界：

```text
4A 负责“读取事实和坐标”；
4B 负责“解释结构和语义”；
4B 不允许修改 4A 的原始 cell value、formula、source_anchor。
```

---

### 2.2 生成候选逻辑块

结构解析后，先生成候选逻辑块。

Word 候选块：

```text
一个章节
一个小节
一个表格
一个表格加前后解释段落
一个附件说明区域
```

Excel 候选块：

```text
一个 sheet 概览块
一个表格岛
一个 key-value 区域
一个联系人区域
一个设备清单区域
一个备注说明区域
一个附件索引区域
```

当前能力知识库的第一优先切分策略：

```text
一行能力项 = 一个可检索 segment；
sheet 标题 = 父级概览 segment；
合并单元格类别 = 行级 segment 的 category_path；
证明材料列 = 行级 segment 的 proof_attachments；
Word 情况说明 = 按章节和表格切分为补充 segment。
```

能力清单行级 segment 的内容边界：

```text
包含：数据中心、楼号、sheet、行号、类别、能力描述、是否满足具体值、备注；
不包含：图片 OCR 文本；
挂载：同一行证明材料列中的图片、嵌入对象、附件引用；
不拆分：同一行的能力描述和是否满足值不能拆成两个 segment；
不合并：不同数据中心、不同楼号、不同能力项不能合并为一个 segment。
```

示例：

```json
{
  "candidate_block_id": "cand_xixian_2_row_054",
  "block_type": "excel_capability_row",
  "data_center_id": "xixian_2",
  "sheet_name": "西咸数据中心2号楼",
  "range": "A54:E54",
  "category_path": ["供配电", "UPS/HVDC不间断电源"],
  "raw_text": "供配电 / UPS/HVDC不间断电源 / IT-UPS、动力-UPS是否为并机系统 / 2N架构，UPS均为单机系统",
  "proof_attachment_count": 1
}
```

示例：

```json
{
  "candidate_block_id": "cand_008",
  "file_id": "file_001",
  "block_type": "excel_table_island",
  "sheet_name": "供配电",
  "range": "B8:H22",
  "raw_text": "设备名称 | 型号 | 数量 | 容量 ..."
}
```

回测指标：

```text
候选块覆盖率
候选块边界准确率
表格岛识别准确率
key-value 区域识别准确率
```

验收标准：

```text
候选块应尽量覆盖完整语义单元；
不要把一个设备清单切成多个无意义碎片；
不要把多个无关区域合成一个大块。
```

---

### 2.3 智能体判断每个候选块讲什么

对每个候选块调用智能体，让它输出结构化判断。

智能体输入：

```json
{
  "file_name": "数据中心工勘资料.xlsx",
  "sheet_name": "供配电",
  "range": "B8:H22",
  "parent_summary": "该文档为数据中心建设工勘资料，包含基础信息、供配电、空调、网络、消防等内容。",
  "raw_text": "..."
}
```

智能体输出：

```json
{
  "main_topic": "UPS设备配置",
  "summary": "该表格描述 UPS 设备的型号、容量、数量、安装位置和备注信息。",
  "keywords": ["UPS", "不间断电源", "容量", "数量", "安装位置"],
  "entities": ["UPS", "200kVA", "2台"],
  "possible_fields": [
    "UPS是否配置",
    "UPS数量",
    "UPS容量",
    "UPS型号"
  ],
  "should_split": false,
  "split_reason": "该表格整体描述同一类设备配置，不应继续拆分。"
}
```

如果一个候选块内部包含多个主题，智能体可以建议继续拆分：

```json
{
  "should_split": true,
  "sub_blocks": [
    {
      "range": "A1:F10",
      "topic": "机房基础信息"
    },
    {
      "range": "A12:F25",
      "topic": "供配电信息"
    },
    {
      "range": "A28:F40",
      "topic": "空调系统信息"
    }
  ]
}
```

回测指标：

```text
主题识别准确率
逻辑切分边界准确率
possible_fields 命中率
summary 可用率
```

验收标准：

```text
智能体只负责语义判断；
不能改变原文；
不能丢失原始位置；
每个切分结果必须有来源锚点。
```

---

## 3. 下层文档切分时，打上父文档和祖文档标签

### 3.1 给每个片段打 lineage 标签

每个切分后的片段都必须知道自己来自哪里。

输出：

```json
{
  "segment_id": "seg_012",
  "file_id": "file_003",
  "parent_file_id": "file_001",
  "ancestor_file_ids": ["file_001"],
  "level": 1,
  "lineage_path": [
    "数据中心工勘资料.xlsx",
    "Sheet: 附件目录",
    "Object: 附件2_设备清单.docx",
    "Section: 供配电系统",
    "Table: UPS配置表"
  ],
  "source_anchor": {
    "file_name": "附件2_设备清单.docx",
    "page": 3,
    "table_index": 2
  }
}
```

回测指标：

```text
parent_file_id 准确率
ancestor_file_ids 准确率
lineage_path 准确率
source_anchor 可回溯率
```

验收标准：

```text
任意一个 segment 都能回到原始文档位置；
任意一个下层 segment 都知道自己的父文档和祖文档。
```

---

### 3.2 给每个片段打语义标签

每个片段需要包含语义标签。

输出：

```json
{
  "segment_id": "seg_012",
  "main_topic": "UPS设备配置",
  "summary": "该表格描述 UPS 的型号、容量、数量和安装位置。",
  "keywords": ["UPS", "容量", "数量", "供配电"],
  "entities": ["UPS", "200kVA", "2台"],
  "possible_fields": [
    "UPS是否配置",
    "UPS数量",
    "UPS容量",
    "UPS型号"
  ]
}
```

回测指标：

```text
topic 准确率
keyword 命中率
entity 抽取准确率
possible_fields 命中率
```

验收标准：

```text
summary 只能作为检索辅助；
最终答案不能只引用 summary；
最终答案必须引用 raw_text 或原始单元格。
```

### 3.2.1 图片证据打标规则

图片只作为文字证据的附件佐证，不做 OCR，不参与向量化，不单独触发答案生成。

规则：

```text
如果一行文字 segment 命中，则返回该行 proof_attachments；
如果只命中图片但没有文字证据，不生成答案；
图片不写入 embedding_text；
图片不进入关键词索引；
图片进入审计记录、人工复核报告、附件导出结果；
最终答案中的事实只能来自 raw_text、单元格文本或 Word 段落/表格文本。
```

附件证据结构：

```json
{
  "attachment_id": "att_00054_01",
  "segment_id": "seg_xixian_2_row_054",
  "attachment_type": "image",
  "proof_role": "supporting_material",
  "source_cell": "E54",
  "image_id": "ID_xxx",
  "media_path": "xl/media/image54.png",
  "original_formula": "=_xlfn.DISPIMG(\"ID_xxx\",1)",
  "ocr_status": "not_required",
  "used_for_generation": false,
  "used_for_audit": true
}
```

Evidence Packet 中的图片输出：

```json
{
  "segment_id": "seg_xixian_2_row_054",
  "raw_text": "IT-UPS、动力-UPS是否为并机系统；2N架构，UPS均为单机系统",
  "proof_attachments": [
    {
      "attachment_id": "att_00054_01",
      "media_path": "xl/media/image54.png",
      "source_cell": "E54"
    }
  ]
}
```

验收标准：

```text
图片附件不能改变答案内容；
图片附件必须能从答案审计记录回到原始文件、sheet、cell；
没有文字证据时，图片不能单独支撑自动填写。
```

---

### 3.3 下层片段继承父文档上下文

下层文档切分时，要把父文档和祖文档的上下文带下来。

例如父文档说明：

```text
附件2为某数据中心项目的设备配置清单。
```

下层片段是：

```text
UPS | 200kVA | 2台
```

单独看下层片段时，信息不完整。需要继承父上下文：

```json
{
  "segment_id": "seg_012",
  "raw_text": "UPS | 200kVA | 2台",
  "parent_context": "附件2为某数据中心项目的设备配置清单。",
  "ancestor_context": "顶层文档为中国移动某数据中心工勘资料。",
  "expanded_text_for_retrieval": "中国移动某数据中心工勘资料。附件2为设备配置清单。UPS | 200kVA | 2台。"
}
```

回测指标：

```text
父上下文继承准确率
expanded_text 检索有效率
下层片段语义完整率
```

验收标准：

```text
下层片段不应孤立入库；
每个下层片段都必须带父级语义背景。
```

---

### 3.4 父片段也要打标和更新摘要

不仅子片段要打标，父片段也要知道自己下面包含了什么。

父片段初始摘要：

```text
该章节描述供配电系统。
```

子片段解析完成后，父片段更新为：

```json
{
  "segment_id": "parent_seg_004",
  "summary": "该章节描述供配电系统，子内容包括市电接入、UPS配置、柴油发电机配置和配电柜信息。",
  "child_digest": [
    "子片段 seg_012 描述 UPS 型号、容量和数量",
    "子片段 seg_013 描述柴油发电机配置",
    "子片段 seg_014 描述市电接入方式"
  ],
  "keywords": ["供配电", "市电", "UPS", "柴油发电机", "配电柜"],
  "possible_fields": [
    "供电方式",
    "UPS是否配置",
    "柴油发电机是否配置",
    "配电柜数量"
  ]
}
```

回测指标：

```text
父摘要覆盖率
child_digest 准确率
父节点路由命中率
```

验收标准：

```text
父片段摘要要能帮助后续从上到下检索；
父片段不能只写“本节介绍相关内容”这种空泛描述。
```

---

## 4. 把切分后的文档准备向量化

每个片段最终形成一个标准化记录。

标准记录：

```json
{
  "segment_id": "seg_012",
  "raw_text": "UPS | 200kVA | 2台",
  "summary": "该表格行描述 UPS 设备数量和容量。",
  "embedding_text": "项目：某数据中心。章节：供配电系统。表格：UPS配置。内容：UPS | 200kVA | 2台。",
  "file_id": "file_003",
  "parent_file_id": "file_001",
  "ancestor_file_ids": ["file_001"],
  "parent_segment_id": "seg_parent_004",
  "level": 1,
  "lineage_path": [
    "数据中心工勘资料.xlsx",
    "附件2_设备清单.docx",
    "供配电系统",
    "UPS配置表"
  ],
  "source_anchor": {
    "file_name": "附件2_设备清单.docx",
    "page": 3,
    "table_index": 2,
    "row_index": 4
  },
  "main_topic": "UPS设备配置",
  "keywords": ["UPS", "200kVA", "2台", "供配电"],
  "possible_fields": ["UPS数量", "UPS容量", "UPS是否配置"]
}
```

当前能力知识库的行级标准记录：

```json
{
  "segment_id": "seg_xixian_2_worksheet_001_row_054",
  "segment_type": "excel_capability_row",
  "data_center_id": "xixian_2",
  "data_center_name": "西咸数据中心2号楼",
  "document_role": "knowledge_base",
  "file_id": "file_xixian_2_kb",
  "file_name": "西咸数据中心2号楼维护能力知识库.xlsx",
  "sheet_name": "西咸数据中心2号楼",
  "row_index": 54,
  "category_path": ["供配电", "UPS/HVDC不间断电源"],
  "capability_desc": "IT-UPS、动力-UPS是否为并机系统",
  "answer_value": "2N架构，UPS均为单机系统",
  "raw_text": "供配电 / UPS/HVDC不间断电源 / IT-UPS、动力-UPS是否为并机系统 / 2N架构，UPS均为单机系统",
  "summary": "该行说明 UPS 是否为并机系统及当前架构。",
  "embedding_text": "西咸数据中心2号楼。供配电。UPS/HVDC不间断电源。问题：IT-UPS、动力-UPS是否为并机系统。现状：2N架构，UPS均为单机系统。",
  "keywords": ["UPS", "HVDC", "不间断电源", "并机系统", "2N架构"],
  "entities": ["UPS", "HVDC", "2N"],
  "possible_fields": [
    "UPS是否为并机系统",
    "UPS架构",
    "不间断电源配置"
  ],
  "proof_attachments": [
    {
      "attachment_id": "att_xixian_2_row_054_e",
      "attachment_type": "image",
      "source_cell": "E54",
      "image_id": "ID_xxx",
      "media_path": "xl/media/image54.png",
      "ocr_status": "not_required"
    }
  ],
  "source_anchor": {
    "file_name": "西咸数据中心2号楼维护能力知识库.xlsx",
    "sheet_name": "西咸数据中心2号楼",
    "row_index": 54,
    "cell_range": "A54:E54",
    "text_cells": ["B54", "C54", "D54"],
    "proof_cells": ["E54"]
  },
  "parse_status": "ok"
}
```

注意：

```text
raw_text 用于最终证据；
summary 用于路由；
embedding_text 用于向量化；
lineage_path 用于回溯；
source_anchor 用于定位原始文件；
proof_attachments 用于审计和附件佐证，不用于生成事实。
```

回测指标：

```text
segment 记录完整率
source_anchor 可定位率
embedding_text 语义完整率
metadata 缺失率
```

验收标准：

```text
每个 segment 必须有 raw_text；
每个 segment 必须有 source_anchor；
每个下层 segment 必须有 parent_file_id 和 lineage_path；
当前能力知识库 segment 必须有 data_center_id；
图片证明材料必须以 proof_attachments 挂载到文字 segment。
```

---

## 5. RAG 时按照从上向下的顺序做

后续检索不直接从所有碎片里乱搜，而是优先自上而下。

当前数据集必须先做数据中心路由，再做检索。

路由输入优先级：

```text
1. 用户显式选择的数据中心或楼号；
2. 工勘单文件名、sheet 名、标题中的机房名称；
3. 工勘单已填写字段中的机房地址、机房名称、楼号；
4. 人工选择兜底。
```

路由输出：

```json
{
  "data_center_id": "xixian_2",
  "matched_alias": "西咸数据中心2号楼",
  "confidence": 0.96,
  "route_source": "form_title",
  "allowed_partitions": ["xixian_2"]
}
```

验收标准：

```text
如果 data_center_id 高置信命中，只在对应分库检索；
如果无法判断 data_center_id，必须要求人工选择，不能跨 9 个库混检后自动填写；
如果一个工勘单明确涉及多个楼号，必须拆成多个 field batch，分别路由；
答案的 evidence_items 必须来自同一个 data_center_id，除非字段明确要求跨库对比。
```

基本流程：

```text
字段问题
→ 判断目标数据中心分库
→ 检索顶层父节点
→ 找到相关章节/附件
→ 下钻到子文档
→ 检索子片段
→ 命中原始证据
→ 带上父级上下文生成答案
```

示例：

```text
问题：UPS是否配置？容量多少？

第一步：检索顶层父节点
命中：供配电系统章节

第二步：下钻子节点
命中：附件2_设备清单.docx

第三步：继续下钻
命中：UPS配置表

第四步：返回证据
UPS | 200kVA | 2台
```

为了避免父节点漏召回，可以保留一个兜底路径：

```text
主路径：自上而下检索
兜底路径：叶子片段直接召回
最终证据：必须经过 lineage 回溯补全父上下文
```

回测指标：

```text
父节点召回率
子节点召回率
Evidence Recall@1
Evidence Recall@3
Evidence Recall@5
lineage expansion 准确率
```

验收标准：

```text
检索结果必须能说明它来自哪个父节点；
不能只返回孤立片段；
答案证据必须能回到原始文档。
```

---

# 二、向量化

## 6. 用 embedding 模型做向量化

向量化对象不是原始文件，而是上一步得到的标准 segment。

输入：

```json
{
  "segment_id": "seg_012",
  "embedding_text": "项目：某数据中心。章节：供配电系统。表格：UPS配置。内容：UPS | 200kVA | 2台。"
}
```

输出：

```json
{
  "segment_id": "seg_012",
  "embedding": [0.012, -0.034, 0.088]
}
```

embedding_text 的构造原则：

```text
必须包含原文；
必须包含父级主题；
必须包含章节路径；
Excel 片段必须包含 sheet、表头、行列上下文；
不要只 embedding summary；
必须包含 data_center_id 或数据中心名称；
不得包含图片 OCR 结果；
不得只写证明材料公式。
```

当前能力清单 embedding_text 模板：

```text
{数据中心名称}。{文件名}。{sheet_name}。{category_path}。
能力描述：{capability_desc}。
现状/答案：{answer_value}。
来源：第 {row_index} 行。
```

示例：

```text
西咸数据中心2号楼。西咸数据中心2号楼维护能力知识库.xlsx。西咸数据中心2号楼。供配电 / UPS/HVDC不间断电源。
能力描述：IT-UPS、动力-UPS是否为并机系统。
现状/答案：2N架构，UPS均为单机系统。
来源：第 54 行。
```

回测指标：

```text
embedding 生成成功率
embedding_text 为空比例
embedding_text 上下文完整率
```

验收标准：

```text
所有可检索 segment 都必须生成 embedding；
embedding_text 不能脱离原始证据。
```

---

## 7. 存入 Milvus 或 PostgreSQL

早期建议优先使用：

```text
PostgreSQL + pgvector
```

原因：

```text
文档片段不仅有向量，还有大量结构化 metadata；
需要按 parent_id、level、file_type、topic、source_anchor 查询；
需要保存工勘单字段、证据、回写结果、审计日志；
PostgreSQL 更适合统一管理。
```

当前数据集的“分库”建议先实现为逻辑分库：

```text
一个 PostgreSQL 实例；
一套统一表结构；
按 data_center_id 做 partition 或 collection namespace；
检索入口强制带 data_center_id；
后续数据量增大时再物理拆库或迁移到独立向量 collection。
```

不建议第一版直接做 9 个完全独立物理库，原因：

```text
字段、schema、索引和回写审计逻辑完全相同；
物理拆库会增加迁移、回测、权限和备份复杂度；
逻辑 partition 已经可以避免串库检索；
后续可以按 data_center_id 平滑迁移。
```

逻辑分库清单：

```text
xixian_1
xixian_2
xixian_3
xixian_4
xixian_5
xixian_6
xian
chengdong_baqiao
xianyang
```

后期数据量大时，可以切换或增加：

```text
Milvus
Qdrant
Elasticsearch / OpenSearch
```

入库字段建议：

```json
{
  "segment_id": "seg_012",
  "data_center_id": "xixian_2",
  "partition_key": "xixian_2",
  "segment_type": "excel_capability_row",
  "embedding": "...",
  "raw_text": "...",
  "summary": "...",
  "embedding_text": "...",
  "file_id": "file_003",
  "parent_file_id": "file_001",
  "parent_segment_id": "seg_parent_004",
  "ancestor_file_ids": ["file_001"],
  "level": 1,
  "lineage_path": "...",
  "source_anchor": "...",
  "proof_attachments": "...",
  "main_topic": "UPS设备配置",
  "keywords": ["UPS", "容量", "数量"],
  "possible_fields": ["UPS数量", "UPS容量"]
}
```

回测指标：

```text
入库成功率
metadata 完整率
向量检索可用率
按 parent_id 查询成功率
按 topic 过滤成功率
按 data_center_id 分库过滤成功率
```

验收标准：

```text
能按向量召回；
能按父子层级过滤；
能按 source_anchor 回溯；
能按 segment_id 找回完整原文；
所有检索必须可限定到单个 data_center_id；
同名字段不能跨数据中心串库返回。
```

---

## 8. 建立辅助索引

不要只依赖向量索引。还需要建立辅助索引。

建议至少包含：

```text
向量索引：语义召回
关键词索引：设备型号、字段名、专有名词
结构索引：file / sheet / range / page / table
实体索引：UPS、柴油发电机、空调、机房面积、联系人
父子索引：parent_segment_id → child_segment_ids
分库路由索引：数据中心名称、楼号、别名、地址
附件索引：segment_id → proof_attachment_ids
```

示例：

```json
{
  "entity": "UPS",
  "segment_ids": ["seg_012", "seg_018", "seg_021"]
}
```

回测指标：

```text
关键词召回率
实体抽取准确率
父子索引完整率
结构索引可定位率
```

验收标准：

```text
输入一个字段问题时，系统可以同时用向量、关键词、父子关系进行召回；
不能只有 embedding 一条路径；
检索前必须先用分库路由索引确定 data_center_id；
命中文字 segment 后必须能取回对应 proof_attachments。
```

---

# 三、工勘单阅读

## 9. 使用智能体阅读工勘单整体内容

工勘单本身也需要被解析，不能直接当成普通 Excel 填空。

输入：

```text
甲方工勘单.xlsx
甲方工勘单.docx
```

第一步先做结构解析。

对于 Excel 工勘单，需要识别：

```text
sheet 名
表头
合并单元格
问题列
填写说明列
填写样例列
待填写列
备注列
必填标记
空白单元格
```

对于 Word 工勘单，需要识别：

```text
章节
表格
问题项
填写说明
空白区域
示例答案
```

输出：

```json
{
  "form_id": "form_001",
  "file_name": "甲方工勘单.xlsx",
  "route_candidates": [
    {
      "data_center_id": "xixian_2",
      "matched_text": "西咸数据中心2号楼",
      "confidence": 0.96
    }
  ],
  "sheets": [
    {
      "sheet_name": "基础信息",
      "summary": "该 sheet 包含机房基础信息、供配电、空调和网络接入字段。"
    }
  ]
}
```

回测指标：

```text
sheet 识别完整率
表头识别准确率
问题列识别准确率
待填写列识别准确率
样例列识别准确率
数据中心路由准确率
```

验收标准：

```text
必须知道哪些位置需要填写；
必须知道每个待填位置对应的问题是什么；
必须保留目标单元格位置；
必须给出 data_center_id 或 route_candidates；
路由不明确时不能自动批量填写。
```

---

## 10. 识别工勘单需要填写什么

对工勘单中的每个待填写项，构建字段任务。

字段任务格式：

```json
{
  "field_id": "field_001",
  "data_center_id": "xixian_2",
  "question": "机房是否配置UPS？",
  "instruction": "如配置，请填写数量和容量。",
  "example": "是，配置2台200kVA UPS",
  "target_location": {
    "file_name": "甲方工勘单.xlsx",
    "sheet_name": "基础信息",
    "cell": "D18"
  },
  "context_path": [
    "基础信息",
    "供配电系统",
    "UPS配置"
  ],
  "expected_answer_type": "boolean_with_equipment_detail",
  "retrieval_scope": {
    "allowed_data_center_ids": ["xixian_2"],
    "allow_cross_center_search": false
  },
  "required": true
}
```

如果没有样例：

```json
{
  "field_id": "field_002",
  "question": "机房面积是多少？",
  "instruction": "填写实际面积，单位平方米。",
  "example": null,
  "target_location": {
    "sheet_name": "基础信息",
    "cell": "D12"
  },
  "expected_answer_type": "number_with_unit"
}
```

回测指标：

```text
字段识别准确率
target_location 准确率
question 抽取准确率
instruction 抽取准确率
required 判断准确率
```

验收标准：

```text
每个待填字段必须有 field_id；
每个字段必须知道写到哪里；
每个字段必须知道要问什么。
```

---

## 11. 判断是否存在填写样例，并抽象填写格式

有的工勘单前三列可能是：

```text
问题
填写样例
待填写空行
```

此时样例非常重要。

不要只把样例直接塞进 prompt，而是先从样例中抽象出格式。

例如：

```text
问题：机房是否配置UPS？
样例：是，配置2台200kVA UPS
```

抽象为：

```json
{
  "answer_format": "boolean_with_equipment_detail",
  "format_pattern": "{是否配置}，配置{数量}{容量}{设备类型}",
  "required_slots": ["是否配置", "数量", "容量", "设备类型"]
}
```

再例如：

```text
问题：供电方式
样例：双路市电+柴油发电机备电
```

抽象为：

```json
{
  "answer_format": "power_supply_summary",
  "format_pattern": "{市电路数}+{备电方式}",
  "required_slots": ["市电路数", "备电方式"]
}
```

如果没有样例，则根据问题和说明推断格式：

```json
{
  "answer_format": "plain_text",
  "format_pattern": null,
  "required_slots": []
}
```

回测指标：

```text
样例识别准确率
format_pattern 抽取准确率
required_slots 抽取准确率
格式分类准确率
```

验收标准：

```text
有样例时必须记录 example；
有样例时优先从样例归纳答案格式；
不能只让模型自由模仿样例。
```

---

# 四、工勘单填写

## 12. 开始回溯：根据字段问题从上到下检索答案片段

对每个字段任务单独执行检索，不要整表一次性生成。

输入：

```json
{
  "field_id": "field_001",
  "data_center_id": "xixian_2",
  "question": "机房是否配置UPS？",
  "example": "是，配置2台200kVA UPS",
  "required_slots": ["是否配置", "数量", "容量", "设备类型"]
}
```

### 12.1 先构造检索问题

如果有样例，需要结合样例中的 slot 构造检索问题。

原始问题：

```text
机房是否配置UPS？
```

根据样例扩展后：

```text
查找该项目是否配置 UPS，以及 UPS 的数量、容量、设备类型。
```

结构化检索计划：

```json
{
  "field_id": "field_001",
  "data_center_id": "xixian_2",
  "search_queries": [
    "UPS 配置 数量 容量",
    "不间断电源 型号 数量 容量",
    "供配电 UPS 设备清单"
  ],
  "target_topics": ["供配电", "UPS设备配置"],
  "required_slots": ["是否配置", "数量", "容量", "设备类型"],
  "retrieval_scope": {
    "partition_key": "xixian_2",
    "allow_cross_center_search": false
  }
}
```

回测指标：

```text
检索 query 有效率
target_topics 命中率
required_slots 覆盖率
```

验收标准：

```text
检索问题不能只复述工勘单问题；
必须根据样例判断还需要找哪些子信息。
```

---

### 12.2 从上向下 RAG

检索顺序：

```text
第零层：确认 data_center_id，并锁定分库
第一层：顶层文档摘要
第二层：相关章节或附件
第三层：具体子文档
第四层：具体片段、表格行、单元格
```

示例：

```text
字段：机房是否配置UPS？

顶层命中：
数据中心工勘资料.xlsx / 供配电相关附件

下钻命中：
附件2_设备清单.docx

继续下钻：
供配电系统 / UPS配置表

最终证据：
UPS | 200kVA | 2台
```

输出 Evidence Packet：

```json
{
  "field_id": "field_001",
  "data_center_id": "xixian_2",
  "evidence_items": [
    {
      "segment_id": "seg_012",
      "raw_text": "UPS | 200kVA | 2台",
      "summary": "该表格行描述 UPS 数量和容量。",
      "lineage_path": [
        "数据中心工勘资料.xlsx",
        "附件2_设备清单.docx",
        "供配电系统",
        "UPS配置表"
      ],
      "source_anchor": {
        "file_name": "附件2_设备清单.docx",
        "page": 3,
        "table_index": 2,
        "row_index": 4
      },
      "proof_attachments": [
        {
          "attachment_id": "att_00054_01",
          "attachment_type": "image",
          "media_path": "xl/media/image54.png",
          "source_cell": "E54",
          "ocr_status": "not_required"
        }
      ]
    }
  ]
}
```

回测指标：

```text
父节点召回率
子节点召回率
最终证据 Recall@1
最终证据 Recall@3
最终证据 Recall@5
错误证据率
跨库误召回率
图片附件挂载准确率
```

验收标准：

```text
每个字段必须返回 evidence_items；
没有证据不能直接生成答案；
证据必须包含 lineage_path 和 source_anchor；
证据必须来自字段任务指定的 data_center_id；
proof_attachments 只能作为附件随证据返回，不能单独作为答案依据。
```

---

## 13. 按样例拼接 prompt，生成答案，并做校验和回写

### 13.1 根据样例和证据构造 prompt

如果工勘单提供样例：

```text
样例：是，配置2台200kVA UPS
```

则 prompt 应包含：

```text
问题：机房是否配置UPS？
填写样例：是，配置2台200kVA UPS
请按照样例格式填写。
证据：
1. UPS | 200kVA | 2台
要求：
- 只能根据证据填写
- 不要补充证据中没有的信息
- 如果证据不足，返回 unknown
- 图片附件只作为证明材料随审计记录输出，不得从图片中推断新事实
```

更结构化的 prompt：

```text
你需要填写一个工勘单字段。

字段问题：
机房是否配置UPS？

填写样例：
是，配置2台200kVA UPS

样例格式：
{是否配置}，配置{数量}{容量}{设备类型}

证据：
[1] UPS | 200kVA | 2台
来源：数据中心工勘资料.xlsx -> 附件2_设备清单.docx -> 供配电系统 -> UPS配置表
附件佐证：[att_00054_01] xl/media/image54.png

请输出 JSON：
{
  "answer": "",
  "used_evidence_ids": [],
  "supporting_attachment_ids": [],
  "is_supported": true/false,
  "reason": ""
}
```

输出：

```json
{
  "answer": "是，配置2台200kVA UPS",
  "used_evidence_ids": ["seg_012"],
  "supporting_attachment_ids": ["att_00054_01"],
  "is_supported": true,
  "reason": "证据中存在 UPS，数量为2台，容量为200kVA。"
}
```

回测指标：

```text
答案准确率
样例格式符合率
证据引用准确率
unsupported 正确拒答率
附件引用准确率
```

验收标准：

```text
答案必须引用证据；
答案格式必须符合样例；
证据不足时必须拒填；
图片附件不能贡献答案事实，只能作为 supporting_attachment_ids 输出。
```

---

### 13.2 没有样例时，按字段说明生成答案

如果没有样例，则使用问题和填写说明。

输入：

```json
{
  "question": "机房面积是多少？",
  "instruction": "填写实际面积，单位平方米。",
  "example": null,
  "evidence": [
    "机房面积：120平方米"
  ]
}
```

输出：

```json
{
  "answer": "120平方米",
  "used_evidence_ids": ["seg_025"],
  "is_supported": true
}
```

回测指标：

```text
无样例字段答案准确率
单位保留准确率
格式错误率
```

验收标准：

```text
没有样例时，不追求复杂表达；
优先填写短、准、可证据支持的答案。
```

---

### 13.3 校验答案是否被证据支持

生成答案后，必须校验。

校验内容：

```text
答案中的每个关键信息是否都能在证据中找到；
数量是否一致；
单位是否一致；
设备名称是否一致；
是否有证据冲突；
是否符合样例格式；
是否符合目标单元格要求；
证据是否来自字段任务指定的 data_center_id；
supporting_attachment_ids 是否全部属于 used_evidence_ids 对应的 segment。
```

示例：

```json
{
  "field_id": "field_001",
  "data_center_id": "xixian_2",
  "answer": "是，配置2台200kVA UPS",
  "validation": {
    "evidence_supported": true,
    "format_valid": true,
    "data_center_consistent": true,
    "attachments_bound_to_evidence": true,
    "conflict_detected": false,
    "needs_human_review": false
  }
}
```

如果证据冲突：

```json
{
  "field_id": "field_001",
  "answer": null,
  "validation": {
    "evidence_supported": false,
    "conflict_detected": true,
    "conflict_items": [
      {
        "value": "配置2台UPS",
        "source": "设备清单.xlsx"
      },
      {
        "value": "未配置UPS",
        "source": "现场说明.docx"
      }
    ],
    "needs_human_review": true
  }
}
```

回测指标：

```text
证据支持判断准确率
冲突识别准确率
格式校验准确率
误填率
拒填准确率
分库一致性校验准确率
附件归属校验准确率
```

验收标准：

```text
有冲突时不能强行填写；
证据不足时不能强行填写；
校验失败的字段进入人工确认；
证据 data_center_id 与字段任务不一致时必须拒填。
```

---

### 13.4 生成回写 Patch

不要直接覆盖原工勘单，先生成 patch。

Patch 格式：

```json
{
  "patch_id": "patch_001",
  "field_id": "field_001",
  "data_center_id": "xixian_2",
  "target_location": {
    "file_name": "甲方工勘单.xlsx",
    "sheet_name": "基础信息",
    "cell": "D18"
  },
  "old_value": "",
  "new_value": "是，配置2台200kVA UPS",
  "used_evidence_ids": ["seg_012"],
  "supporting_attachment_ids": ["att_00054_01"],
  "status": "ready_to_write"
}
```

回测指标：

```text
target_location 准确率
old_value 读取准确率
new_value 写入准确率
patch 可应用率
```

验收标准：

```text
每个答案都必须先生成 patch；
patch 必须记录旧值、新值、目标位置和证据来源；
patch 必须记录 data_center_id；
patch 可以记录图片附件 ID，但不能把图片内容写入答案单元格。
```

---

### 13.5 回写工勘单并生成审计记录

应用 patch 后，生成新文件和审计记录。

审计记录：

```json
{
  "field_id": "field_001",
  "data_center_id": "xixian_2",
  "question": "机房是否配置UPS？",
  "answer": "是，配置2台200kVA UPS",
  "target_cell": "D18",
  "evidence": [
    {
      "text": "UPS | 200kVA | 2台",
      "lineage_path": [
        "数据中心工勘资料.xlsx",
        "附件2_设备清单.docx",
        "供配电系统",
        "UPS配置表"
      ],
      "attachments": [
        {
          "attachment_id": "att_00054_01",
          "media_path": "xl/media/image54.png",
          "source_cell": "E54",
          "ocr_status": "not_required"
        }
      ]
    }
  ],
  "validation": {
    "evidence_supported": true,
    "format_valid": true
  }
}
```

回测指标：

```text
Excel 回写成功率
格式保持率
审计记录完整率
人工可复核率
```

验收标准：

```text
回写后不破坏原表格式；
能从每个填写结果追溯到证据；
能输出一份人工审核报告。
```

---

# 最终实现顺序

建议严格按下面顺序做，不要一开始直接端到端：

```text
第一版主路径：
1. 原始文件登记，并识别 document_role
2. 建立 9 个 data_center_id 与别名表
3. 将知识库文件路由到对应 data_center_id
4. Excel worksheet XML 结构解析
5. 能力清单行级记录抽取
6. 合并单元格类别向下传播
7. WPS DISPIMG 图片 ID 与 media_path 打标
8. 行级 segment 标准化
9. 按 data_center_id 逻辑分库入库
10. embedding、关键词、结构、附件索引建立
11. 工勘单结构阅读和 data_center_id 路由
12. 字段任务和样例格式识别
13. 分库内字段级证据检索
14. 按样例生成答案、校验、patch 回写
15. 输出审计记录和图片附件清单

后续增强路径：
16. Word 情况说明深度切分和补充索引
17. 嵌套 docx/xlsx/pptx 递归抽取
18. 父子层级摘要和 child_digest 更新
19. 跨文档冲突检测
20. 更复杂的多库对比类字段支持
```

每一步都要单独保存中间结果，并单独回测准确率。

---

# 每一步的最低回测表

| 步骤 | 回测重点 | 主要指标 |
|---|---|---|
| 文件登记 | 文件是否全 | 文件登记完整率 |
| 分库路由 | 文件和工勘单是否进对库 | data_center_id 准确率 |
| 结构解析 | Excel 真实单元格是否正确 | cell ref 解析准确率 |
| 行级抽取 | 能力项是否抽全 | 行级记录召回率 |
| 合并单元格 | 类别是否继承正确 | category_path 准确率 |
| 图片打标 | DISPIMG 是否映射到媒体 | 图片附件映射准确率 |
| 逻辑切分 | 切分边界是否合理 | 行级 segment 准确率 |
| 语义打标 | 片段主题是否正确 | topic 准确率 |
| 父子标签 | lineage 是否正确 | lineage_path 准确率 |
| 父摘要更新 | 父节点是否能路由 | 父节点召回率 |
| 向量化 | embedding_text 是否完整 | embedding_text 完整率 |
| 入库 | metadata 是否完整 | 入库成功率、分库过滤成功率 |
| 工勘单阅读 | 待填字段是否识别 | 字段识别准确率、表单路由准确率 |
| 样例识别 | 样例格式是否抽对 | format_pattern 准确率 |
| 证据回溯 | 正确证据是否召回 | Evidence Recall@K |
| 答案填写 | 答案是否准确且可追溯 | 答案准确率、格式符合率、证据支持率 |
| 附件输出 | 图片是否随证据返回 | supporting_attachment 准确率 |

---

# 最终简化版主链路

```text
知识库文档
→ 文件登记和 data_center_id 路由
→ Excel 真实单元格解析
→ 能力清单一行一 segment
→ 同行图片证明材料打标为 proof_attachments
→ segment 标准化
→ 按 data_center_id 逻辑分库入库
→ embedding / 关键词 / 结构 / 附件索引

甲方工勘单
→ 解析表单结构和标题
→ 判断 data_center_id
→ 识别问题、样例、待填空行
→ 构建字段任务

字段填写
→ 根据问题和样例生成检索计划
→ 锁定 data_center_id 分库
→ 检索原始文字证据片段
→ 取回 proof_attachments
→ 按样例格式生成答案
→ 校验证据支持
→ 生成 patch
→ 回写工勘单
→ 输出审计记录和附件清单
```

一句话总结：

这个项目第一版的核心不是“普通 RAG 自动填表”，而是“按数据中心分库构建 Excel 行级能力项索引，命中文字证据后携带图片附件佐证，再根据工勘单字段按样例格式受约束地填写答案”。
