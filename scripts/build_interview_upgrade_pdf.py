"""Build the Chinese research brief. Requires reportlab; no model/network calls."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/pdf/datacenter-research-upgrade-proposals-20261001.pdf"
FONT = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
NAVY = colors.HexColor("#152C42")
TEAL = colors.HexColor("#087F8C")
INK = colors.HexColor("#253D4D")
MUTED = colors.HexColor("#617681")
LINE = colors.HexColor("#DCE5E9")
PALE = colors.HexColor("#F2F7F8")
AMBER = colors.HexColor("#9C6E20")
W, H = A4
BODY_W = W - 88

pdfmetrics.registerFont(TTFont("Brief", str(FONT)))
pdfmetrics.registerFontFamily("Brief", normal="Brief", bold="Brief", italic="Brief", boldItalic="Brief")


def style(name: str, size: float, leading: float, color=INK, **kw):
    return ParagraphStyle(
        name, fontName="Brief", fontSize=size, leading=leading,
        textColor=color, wordWrap="CJK", alignment=TA_LEFT,
        spaceAfter=8, allowWidows=0, allowOrphans=0, **kw,
    )


ST = {
    "body": style("body", 10.3, 16.5),
    "small": style("small", 8.7, 13.5, MUTED),
    "cell": style("cell", 9.2, 14, spaceBefore=0),
    "cellsmall": style("cellsmall", 8.5, 12.5, spaceBefore=0),
    "headcell": style("headcell", 9.3, 14, colors.white),
    "eyebrow": style("eyebrow", 8.3, 13, TEAL, tracking=1.2),
    "title": style("title", 28, 40, NAVY, spaceBefore=8),
    "h1": style("h1", 21, 30, NAVY, spaceBefore=2),
    "h2": style("h2", 13.4, 21, TEAL, spaceBefore=8),
    "quote": style("quote", 12, 20, NAVY),
    "ref": style("ref", 8.2, 12, INK),
}


def p(text: str, kind="body") -> Paragraph:
    return Paragraph(text, ST[kind])


def section(label: str, title: str, num: int):
    return [p(label, "eyebrow"), p(f'<a name="page{num}"/>{title}', "h1"), Spacer(1, 6)]


def table(headers, rows, widths, small=False):
    k = "cellsmall" if small else "cell"
    data = [[p(x, "headcell") for x in headers]]
    data.extend([[p(x, k) for x in row] for row in rows])
    t = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PALE, colors.white]),
        ("LINEBELOW", (0, 0), (-1, 0), .7, NAVY),
        ("LINEBELOW", (0, 1), (-1, -1), .4, LINE),
    ]))
    return t


def callout(title, text):
    t = Table([[p(title, "h2")], [p(text)]], colWidths=[BODY_W], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("LINEBEFORE", (0, 0), (0, -1), 3, TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ]))
    return t


class Architecture(Flowable):
    def __init__(self):
        super().__init__()
        self.width = BODY_W
        self.height = 122

    def draw(self):
        c = self.canv
        names = [("Vue 3", "字段与证据工作台"), ("Go API / Asynq", "权限、任务与归档"), ("Python Step15", "检索、仲裁与写回")]
        bw = (BODY_W - 36) / 3
        for i, (title, desc) in enumerate(names):
            x = i * (bw + 18)
            c.setFillColor(PALE)
            c.setStrokeColor(LINE)
            c.roundRect(x, 54, bw, 59, 6, fill=1, stroke=1)
            c.setFillColor(NAVY)
            c.setFont("Brief", 11)
            c.drawCentredString(x + bw/2, 88, title)
            c.setFillColor(MUTED)
            c.setFont("Brief", 8.6)
            c.drawCentredString(x + bw/2, 69, desc)
            if i < 2:
                c.setStrokeColor(TEAL)
                c.line(x + bw + 3, 83, x + bw + 15, 83)
                c.line(x + bw + 12, 86, x + bw + 15, 83)
                c.line(x + bw + 12, 80, x + bw + 15, 83)
        c.setFillColor(TEAL)
        c.setFont("Brief", 9)
        c.drawString(8, 29, "Qdrant 分层知识检索  /  外部模型服务  /  Excel audit  /  manifest 稳定契约")
        c.setFillColor(MUTED)
        c.setFont("Brief", 8.5)
        c.drawString(8, 10, "三类结果分别可见：原始回答、写回决策、引用定位。保留权限与 checkpoint 续跑。")


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(44, H - 36, W - 44, H - 36)
    canvas.setFillColor(MUTED)
    canvas.setFont("Brief", 7.5)
    canvas.drawString(44, H - 27, "DATACENTER  /  RESEARCH & ENGINEERING")
    canvas.drawRightString(W - 44, H - 27, "2026-10-01")
    canvas.line(44, 42, W - 44, 42)
    canvas.drawString(44, 28, "工勘填报 Agent · 技术迭代与面试建议")
    canvas.setFillColor(TEAL)
    canvas.drawRightString(W - 44, 28, f"{doc.page:02d}")
    canvas.restoreState()


pages = []

# 1: Decision brief.
pages.append([
    p("PROJECT UPGRADE BRIEF  /  决策版", "eyebrow"),
    p("工勘填报 Agent<br/>大厂研究与项目迭代建议", "title"),
    p("面向互联网大厂面试 · 基于当前项目与官方研究证据", "quote"),
    p("调研截止 2026-10-01。近期维护工具与仍适用的 2024-2025 年论文同时纳入；版本、论文首稿与发布页日期分开记录。", "small"),
    Spacer(1, 14),
    callout("推荐定位", "把项目定位为“可核对原文证据的工勘填报 Agent 平台”。保留 Python + Go + Vue + Qdrant，优先补真实模板、索引版本与领域评测，再依据坏例引入结构解析或多模态。"),
    Spacer(1, 12),
    table(["最先做的三件事", "为什么值得做"], [
        ["01  新模板字段分析与版本化索引", "从历史固定表单走向真实上传；构建失败不破坏已有知识库。"],
        ["02  人工 gold、盲测与风险-覆盖评测", "用可复现结果证明正确写回、拒答与成本，形成面试里的实验依据。"],
        ["03  调用预算与每轮动作界面", "将现有受约束动作扩展为可观察、可停止、可解释的长任务。"],
    ], [BODY_W * .43, BODY_W * .57]),
    Spacer(1, 13),
    p("当前已有增量：上传来源进入真实检索；动作过滤实际执行；Python/Go/Vue 原文定位与字段证据工作台。完整框架迁移不作为第一优先级。"),
    p("阅读导航：02 项目诊断 / 03 Google / 04 阿里与解析候选 / 05 建议清单 / 06 技术规格 / 07 评测 / 08 界面 / 09 面试与实施 / 10 官方来源。", "small"),
    p("本报告列的是建议与已验证工程事实。尚无真实模型业务准确率、成本下降或跨机房泛化成绩。", "small"),
])

# 2: Current-state diagnosis.
pages.append(section("01  /  当前项目", "架构稳定，补齐业务闭环", 2) + [
    Architecture(),
    table(["能力", "当前状态", "下一步缺口"], [
        ["上传与召回", "已补四类 uploaded_* 独立检索层；原文与向量输入分离。", "完整结构解析、递归 OLE 仍需按坏例深化。"],
        ["Agent 动作", "已执行来源/层级/namespace 限制；非法动作空结果并记录原因。", "真实 token/call/time 预算和停止收益观测。"],
        ["引用与界面", "逐字定位、SHA256、Unicode 区间，Go 校验与 Vue 高亮已落地。", "专门的逐轮动作视图与索引新鲜度证明。"],
        ["模板与索引", "已有离线分析、DB 版本/current 与 ready 切换；填表仍读历史 Step12，入库覆盖同 namespace。", "上传模板字段穿透；任务固定物理快照；原子发布与真实回滚。"],
        ["评测与部署", "工程契约与匿名合成页面验收已有；现成部署包为旧 ARM64 构建。", "人工领域 gold、真实模型对比；最新镜像重建和部署验收。"],
    ], [80, 216, BODY_W - 296], small=True),
    Spacer(1, 8),
    p("已验证范围", "h2"),
    p("实施记录：Python 250 项测试通过，Go 全量测试通过，Web 23 项测试、类型检查与生产构建通过。浏览器验收覆盖字段筛选、emoji 高亮、键盘操作及 768/390/320 像素布局。"),
    p("这些检查证明工程契约与界面行为；合成 fixture 使用确定性模型替身，不能证明真实业务准确率或成本改善。AgentScope 当前是受控 callback 角色编排，不能描述为原生多模型自主协商。", "small"),
])

# 3: Google.
pages.append(section("02  /  GOOGLE", "优先借鉴证据与评测方法", 3) + [
    table(["官方研究 / 仓库", "项目中的用法", "接入决定"], [
        ["LangExtract [1]<br/>v1.7.0 · 2026-09-13<br/>Apache-2.0", "让模型提供候选 quote，再由代码核对原文、哈希和区间。当前旁路已借鉴这一思想。", "高优先级方法借鉴；不新增框架。默认 fuzzy alignment 不宜直接作为安全写回依据。"],
        ["Sufficient Context [2]<br/>首稿 2024-11-09<br/>v3 2025-04-23", "把“上下文不充分”与“上下文充分但答错”分开统计；评估可回答性与选择性拒答。", "高优先级评测方法；在工勘领域重新标注充分性，不照搬论文成绩。"],
        ["FACTS Suite [3]<br/>官方发布 2025-12-09<br/>Grounding 基础 [16]", "借鉴基础 Grounding 的两阶段评价：任务完成与事实支撑；参考 Suite 的公开/私有盲测。", "高优先级方法借鉴；通用 benchmark 分数不代表本项目效果。"],
        ["ADK [4]<br/>v2.10.0 · 2026-09-25<br/>Apache-2.0", "参考轨迹评测、模型/工具调用数、token 和耗时观测，映射到现有 manifest/trace。", "中优先级参考；现有 AgentScope/Worker 已承担运行时，不立即迁移。"],
    ], [145, 183, BODY_W - 328], small=True),
    Spacer(1, 12),
    callout("关键技术边界", "原文定位只证明这段字存在，不证明它支持答案。机房范围、现状/规划、单台/总量、单位与必需槽仍需原有门控和领域评测。当前定位旁路不改变写回策略。"),
    Spacer(1, 9),
    p("建议实验：同一份冻结知识快照，对上下文充分/不充分分别报告答对、答错、拒答；在 validation 校准阈值，在 test 绘制错误写回风险与覆盖率曲线。", "body"),
    p("A2A 只有在 OCR/检索真正拆成独立 Agent 服务、需要跨服务任务与产物互操作时再评估；同进程角色不需要额外协议。", "small"),
    p("日期口径：LangExtract、ADK 为官方 release feed 的 updated 日期，不等同于精确首次发布日期；ADK release notes 内标 09-24，feed 为 09-25。", "small"),
])

# 4: Alibaba and parser candidates.
pages.append(section("03  /  阿里及结构解析", "深化已有依赖，隔离可选实验", 4) + [
    table(["候选与最新证据", "建议", "当前边界"], [
        ["AgentScope [5]<br/>v2.0.9 · PyPI 上传<br/>2026-09-28 · Apache-2.0", "用事件/middleware 和 pipeline 深化动作日志、预算与状态路由。", "已有 >=2,&lt;3 依赖；SOP 为实验性功能。Runtime 能力已合入 2.0，不另建控制平面 [6]。"],
        ["Qwen3 文本模型 [7]<br/>首稿 2025-06-05<br/>v3 2025-06-11", "当前已用 Qwen3-Embedding-8B；做领域 instruction 和 rerank 候选预算消融。", "不是新引入 embedding；模型卡许可与代码/数据许可分别确认。"],
        ["EvalScope [8]<br/>PyPI 1.12.0<br/>2026-09-16 · Apache-2.0", "在独立离线环境接自定义字段任务、检索指标、Agent trace 与压测。", "先完成领域 gold；LLM judge 需人工校准，不能代替坐标、权限和状态校验。"],
        ["Qwen3-VL [9]<br/>首稿 2026-01-08<br/>v2 2026-01-19", "图片/截图子集做独立 collection、adapter 与成本对比。", "当前客户端只接受字符串；改模型名不等于支持图片。不同向量空间需重建索引。"],
        ["DeepResearch [10]<br/>模型 2025-09-17<br/>报告 v3 2026-05-18", "参考证据驱动补检索和停止策略，受控旁路测试 replanner。", "报告首稿 2025-10-28；网页研究整套流程不直接适配私有工勘字段，不作整体迁移。"],
    ], [151, 168, BODY_W - 319], small=True),
    Spacer(1, 6),
    p("解析候选：按真实坏例接入", "h2"),
    p("Docling v2.131.0（2026-09-29，MIT）[11] 用于 Office/PDF 结构、表格与父子关系；PaddleOCR v3.7.0（2026-06-11，Apache-2.0）[12] 仅为扫描件/图片提供 fallback。原生 Office 优先读结构；递归 OLE 仍需自有适配器。", "small"),
])

# 5: Concrete priorities.
pages.append(section("04  /  修改建议总表", "八项增量，按验收收益排序", 5) + [
    table(["优先级 / 状态", "修改建议", "验收依据 / 面试价值"], [
        ["P0 · 待接入", "新模板字段链路<br/>上传→分析→form_items→Worker；绑定自身 sheet/cell 与合并范围。", "移动行列、重排 sheet 的新模板仍写入正确位置；体现跨模块业务契约。"],
        ["P0 · 待完善", "不可变快照与原子发布/回滚<br/>build → validate → activate；旧版延后清理。", "批量 upsert 中断仍可查询旧版；体现长任务状态、幂等与故障隔离。"],
        ["P0 · 待实施", "领域 gold 与冻结盲测<br/>按机房/文档分组，覆盖拒答、单位、状态与位置。", "输出字段/证据/写回指标及配对差异；证明收益与局限。"],
        ["P1 · 部分已有", "预算与停止策略<br/>补 calls、tokens、wall time 与重复证据停止。", "预算耗尽明确进入复核；缺 usage 保持 unknown；体现成本治理。"],
        ["P1 · 工作台已有", "逐轮动作视图<br/>展示动作约束、新增证据、停止原因、耗时。", "与真实 trace/manifest 一致；支持失败、恢复、旧任务；体现可解释产品设计。"],
        ["P1 · 门控已有", "充分性与写回阈值校准<br/>用领域坏例验证补检索、拒答和复核策略。", "阈值只在 validation 选择；错误风险与覆盖同报；体现业务边界判断。"],
        ["P2 · 待实验", "结构解析/OCR adapter<br/>统一 source_anchor、raw_source_text、父子关系。", "合并表格、宽表、扫描数字坏例分组改善；体现从问题选技术。"],
        ["P2 · 待实验", "图片证据检索<br/>Qwen3-VL 独立图像子集与索引。", "文本 baseline 保留，比较图片问题收益；视觉命中仍不直接放行数值。"],
    ], [89, 211, BODY_W - 300], small=True),
    Spacer(1, 9),
    p("建议先完成前三项，再按失败样本决定解析和多模态；型号/单位检索若是短板，可另做 instruction、sparse/RRF 受控对照 [15]。GraphRAG 与 AutoGen README 已标 maintenance mode [13][14]，不优先迁移；RL/GPU 栈待可信 gold、reward 与资源具备后再研究。", "small"),
])

# 6: Implementation details.
pages.append(section("05  /  P0 技术规格", "模板、索引、测试集各有完成条件", 6) + [
    p("A. 上传模板成为字段位置的唯一来源", "h2"),
    p("入口：src/nested_doc_rag/form/analyze.py、cli.py 与 Go command_builder.go。已有离线分析、CLI <nobr>--form-items</nobr> 覆盖参数和模板物化，但 Go 未透传 form_items 或触发分析；应接通上传→分析→字段→Worker，记录模板哈希，未知布局提供确认。"),
    p("验收：同一问题移动到新列、新 sheet 后位置正确；空标题、重复字段、合并单元格进入可解释复核；公式与非空单元格保护保留；checkpoint 使用稳定字段身份。", "small"),
    p("B. 构建新索引，不先破坏旧知识", "h2"),
    p("入口：ingestion.py、qdrant_retriever.py、Go knowledge/service.go 与 form/payload.go。已有 DB 版本/current 指针，但默认复用物理 namespace，Worker 未穿透版本 ID；旧版 archived 与 ready 限制妨碍常规回滚。构建验证后再发布，保留不可变旧快照，并原子协调状态/current 更新。"),
    p("验收：V2 构建或发布任一步失败，V1 仍可查询且不误发 ready；V1 任务的 target/global 都冻结旧版，跨切换不混入 V2；回滚实际恢复 V1；模型向量空间不混用。", "small"),
    p("C. 领域 gold 与最小坏例集", "h2"),
    p("入口：evaluation/field_metrics.py、gold_fields.jsonl 与独立实验配置。建议起步人工标注 100-150 个字段、20-30 份代表性资料；这是建议规模，不是现有数据量。gold 包含答案、目标 cell、来源 anchor、机房/粒度/时间、单位、必需槽、可回答性与上下文充分性。"),
    p("验收：机房/文档分组划分 train、validation、test；gold 和已填写答案不进入检索或生成 prompt；test 冻结后再运行。无法留出完整机房时明确报告评测局限。", "small"),
    Spacer(1, 8),
    callout("共同不变量", "保留 raw prediction 与 Overlay 分离、owner-only 下载、manifest 校验、公式/重复目标保护、任务取消、checkpoint 续跑与中断状态恢复。新增解析或检索路径必须遵守原有机房范围和来源约束。"),
])

# 7: Evaluation.
pages.append(section("06  /  可证实的技术收益", "先定义指标，再写简历数字", 7) + [
    table(["指标", "定义与证据", "必须同时报告"], [
        ["字段与状态正确率", "实际答案/status 对人工 gold，单位与别名规范透明。", "全字段分母；可回答/不可回答分别统计。"],
        ["检索 Recall@k", "候选集对 gold 文档/anchor，而非只对最终引用 ID。", "错机房、型号、数字、结构类别分项。"],
        ["定位与语义支撑", "分别核对 hash/quote/区间，以及主张是否受正确范围证据支持。", "exact 不是语义正确；source ID 非空不是 grounding。"],
        ["写回 precision", "答案、目标位置与证据均正确的实际写入 / 全部实际写入。", "覆盖率、错误类别、置信区间；零写入时未定义。"],
        ["拒答与风险-覆盖", "不可回答 gold 对拒答；阈值变化时错误风险与覆盖同报。", "validation 校准、test 冻结；partial 不全算正确拒答。"],
        ["效率与成本", "p50/p95、真实 calls/tokens、单字段与新增正确字段成本。", "缺 usage 为 unknown；价格/模型/日期版本化。"],
    ], [103, 211, BODY_W - 314], small=True),
    p("四组先行消融", "h2"),
    p("① 修复上传契约后的单轮分层 RAG；② 加真实动作约束；③ 加动作预算/停止；④ 按坏例只加一种解析或检索方案。固定数据快照、模型、prompt 与配置；研究某因素时仅改该因素。概率模型建议重复 3 次并报告配对差异。"),
    p("当前 provenance 不影响门控，单独报告定位质量与审计收益。若未来将定位用于拒写，需另设 gate 实验，比较 precision 与 coverage，不能归因给当前旁路。", "small"),
    p("必测坏例：正确值但错机房、规划容量当现网、总量填单台、必需槽缺失、重复引用、OCR 8/3、冲突来源、公式/合并/重复目标、新模板移列、checkpoint 中断恢复。", "small"),
])

# 8: Product UI.
shot = ROOT / "artifacts/design/evidence-workbench-live/desktop-exact.png"
im = Image(str(shot), width=BODY_W, height=BODY_W * 717 / 1392)
pages.append(section("07  /  界面美化", "让复核者看清答案、写回与原文", 8) + [
    p("正式 Vue 详情页截图 · 匿名合成契约资料 · 非线上业务效果", "small"),
    im,
    Spacer(1, 10),
    table(["已经落地", "建议继续补充"], [
        ["全字段列表、搜索与状态筛选；原始答案、实际写回和门控理由分别展示。", "逐轮动作面板：动作来源限制、返回/新增 chunk 数、停止原因与耗时。"],
        ["代码校验逐字原文高亮；重复/失配引用不强行高亮；来源逐条可核对。", "来源版本与新鲜度：只有版本化索引完成后再展示可信版本状态。"],
        ["四项主指标、未知进度不虚构；中文原因、旧任务与空/失败状态兼容。", "真实数据驱动的风险-覆盖与成本图；在领域评测完成后展示实验结果。"],
    ], [BODY_W / 2, BODY_W / 2], small=True),
    Spacer(1, 7),
    p("视觉原则：统一字号/间距/状态色，保持左侧选字段、右侧看证据的固定关系；移动端堆叠，提供键盘焦点；长原文安全转义。320/390/768 像素无水平溢出已验收。合成演示应持续标注，不混入真实运行指标。", "small"),
])

# 9: Practical roadmap and interviews.
pages.append(section("08  /  实施与面试", "用工程事实和实验边界讲项目", 9) + [
    table(["阶段", "可审查交付", "完成判据"], [
        ["下一轮 P0", "新模板分析、版本化索引、领域 gold；最小真实端到端样例。", "模板变化正确；索引故障可回滚；冻结集无事实泄漏。"],
        ["随后 P1", "调用预算/停止、每轮动作 UI；坏例需要的解析 adapter。", "动作实际执行；预算耗尽可解释；新路径对 baseline 有证据收益。"],
        ["实验 P2", "EvalScope 离线 adapter、instruction/hybrid/图片子集实验。", "固定变量、配对对比、人工抽检，报质量与成本。"],
        ["发布准备", "重建对应架构镜像、配置校验、部署与业务验收。", "上线前后产物可追溯；新版本实际服务验收。"],
    ], [84, 214, BODY_W - 298], small=True),
    p("推荐五分钟演示", "h2"),
    p("展示一个可定位引用 → 重复引用待消歧 → 引用失配与实际写回分开 → 未找到/错机房复核 → 一次补检索动作 → checkpoint 续跑与真实 Excel audit。当前使用匿名合成案例；后续换成授权脱敏业务样例。"),
    p("可以写进面试材料的当前事实", "h2"),
    p("发现并修复上传来源与生产过滤的契约错误；将动作约束落实到真实查询；设计不侵入预测的引用定位旁路；跨 Python/Go/Vue 统一 Unicode 与哈希；通过归档归属和路径校验保护证据读取。"),
    p("暂不能写：业务准确率提升、成本下降百分比、跨机房泛化、原生多 Agent 自主协商或完整论文复现。待真实盲测后再写 N、precision、coverage、P95 与成本；不要使用未经测量的简历数字。", "small"),
    callout("部署现状", "已存在约 3.20 GiB 的 2026-07-06 Linux ARM64 完整部署包；不含本轮迭代。7 月 10 日修复版完整压缩包与应用镜像归档缺失，现存配置/依赖镜像及 API 补丁不能替代完整包。部署最新代码需重建镜像与压缩包，并匹配目标架构。"),
])

# 10: Human-readable, clickable references.
refs = [
    ("[1] Google LangExtract v1.7.0 · release feed 2026-09-13 · Apache-2.0", "https://github.com/google/langextract/releases/tag/v1.7.0"),
    ("[2] Sufficient Context: A New Lens on RAG Systems · arXiv:2411.06037", "https://arxiv.org/abs/2411.06037"),
    ("[3] Google DeepMind FACTS Benchmark Suite · 2025-12-09", "https://deepmind.google/blog/facts-benchmark-suite-systematically-evaluating-the-factuality-of-large-language-models/"),
    ("[4] Google ADK v2.10.0 · release feed 2026-09-25 · Apache-2.0", "https://github.com/google/adk-python/releases/tag/v2.10.0"),
    ("[5] AgentScope v2.0.9 · PyPI 上传 2026-09-28 · Apache-2.0", "https://github.com/agentscope-ai/agentscope/releases/tag/v2.0.9"),
    ("[6] AgentScope Runtime · README 说明合入 2.0 的迁移状态", "https://github.com/agentscope-ai/agentscope-runtime"),
    ("[7] Qwen3 Embedding / Reranker · 首稿 2025-06-05 / v3 2025-06-11", "https://github.com/QwenLM/Qwen3-Embedding"),
    ("[8] EvalScope PyPI 1.12.0 · 2026-09-16 · Apache-2.0", "https://pypi.org/project/evalscope/1.12.0/"),
    ("[9] Qwen3-VL Embedding / Reranker · 首稿 2026-01-08 / v2 2026-01-19", "https://github.com/QwenLM/Qwen3-VL-Embedding"),
    ("[10] Tongyi DeepResearch · arXiv:2510.24701 · v3 2026-05-18", "https://github.com/Alibaba-NLP/DeepResearch"),
    ("[11] Docling v2.131.0 · 2026-09-29 · MIT", "https://github.com/docling-project/docling/releases/tag/v2.131.0"),
    ("[12] PaddleOCR v3.7.0 · 2026-06-11 · Apache-2.0", "https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0"),
    ("[13] Microsoft GraphRAG · README maintenance mode · MIT", "https://github.com/microsoft/graphrag"),
    ("[14] Microsoft AutoGen · README maintenance mode · 代码 MIT", "https://github.com/microsoft/autogen"),
    ("[15] Qdrant Hybrid Queries · 作为后续 sparse/RRF 实验参考", "https://qdrant.tech/documentation/concepts/hybrid-queries/"),
    ("[16] FACTS Grounding · 基础论文 arXiv:2501.03200 · 首稿 2025-01-06", "https://arxiv.org/abs/2501.03200"),
]
reference_page = section("09  /  官方来源与复核入口", "版本和事实都可回到证据", 10)
for name, url in refs:
    reference_page.append(p(f'{escape(name)}<br/><a href="{escape(url)}" color="#087F8C">{escape(url)}</a>', "ref"))
reference_page += [
    Spacer(1, 4),
    p("项目内审计入口", "h2"),
    p("docs/research/2026-10-01-interview-upgrade.md：完整筛选与规格。<br/>docs/research/2026-10-01-implementation.md：实现、验证命令与限制。<br/>docs/research/2026-10-01-sources.json：121 份官方证据的 URL、获取时间、大小、SHA256；快照已重新校验。", "small"),
    p("许可说明：代码、权重和数据许可分别确认。Qwen 文本模型卡已确认 Apache-2.0，不能据此推断整仓所有资产；论文、FACTS 数据与作者仓库不作为业务 SDK 复制。工具版本是调研截止日可核实状态，不代表未来仍为最新。", "small"),
]
pages.append(reference_page)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT), pagesize=A4, leftMargin=44, rightMargin=44,
        topMargin=54, bottomMargin=57, title="工勘填报 Agent：大厂研究与项目迭代建议",
        author="Datacenter 项目研究", subject="Google、阿里官方研究筛选、修改建议与面试路线",
    )
    story = []
    for i, content in enumerate(pages):
        if i:
            story.append(PageBreak())
        story.extend(content)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(OUT)


if __name__ == "__main__":
    main()
