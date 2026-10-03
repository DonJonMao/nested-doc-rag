# Agentic Evidence-Replanning MAS 设计文档

目标仓库分支：`DonJonMao/nested-doc-rag@ops/docker-worker-python-core`  
建议落地文档路径：`docs/agentic_evidence_replanning_mas_design.md`

## 0. 当前代码基线

当前主链路是 `Step15AgentRunner`。README 明确说明：Step15 layered RAG 是 effect engine；LLM 对 evidence pack 做 answer arbitration；overlay 只提供 trace、checkpoint、review、reference enrichment 和 writeback gate，不改写 raw prediction。当前 `equivalent_mas` 在 `Step15MASController` 中固定执行：`QueryPlannerRole -> EvidenceRetrievalRole -> AnswerArbitrationRole -> OverlayControlRole`。`AnswerArbitrationRole` 已经是真正 on-path 的答案仲裁智能体：它构造 Step15 answer prompt，调用 `runner.call_answer(...)`，再转成 `FieldPrediction`。

本设计的目的不是继续把函数包装成 agent，而是把一次性 RAG 改为 evidence state 驱动的多轮动态图：

```text
retrieve -> arbitrate -> diagnose evidence state -> select next action -> retrieve again -> arbitrate again
```

核心贡献：四类固定 epistemic roles，四类失败状态开关，状态条件化 workflow，可做消融实验。

---

## 1. 设计原则

1. **保持 Step15 原语义**：不破坏现有 `equivalent_mas`、`trace_only`、overlay、writeback gate、review item 产物。
2. **final raw prediction 来自最终仲裁轮**：`predictions_raw.jsonl` 仍写 `FieldPrediction`；新增 agentic trace 记录所有中间轮，不把中间答案混进原 schema。
3. **四类 agent 是认知职能，不是命名包装**：
   - `AnswerArbiter`：答案 belief 与证据诊断。
   - `QueryReplanner`：保持原问题语义不漂移的下一次检索动作生成。
   - `EvidenceRetriever`：执行结构化检索动作并返回新观测。
   - `Skeptic`：主动寻找反证、错配和候选答案冲突证据。
4. **四类状态做成开关**：方便消融，任意关闭某一类 workflow 时，系统必须退化到当前证据下的停止/复核，而不是隐式走其他 workflow。
5. **共享黑板通信**：agent 之间不自由闲聊；只读写结构化 `AgenticMASState`。

---

## 2. 新配置

在 `config/default.yaml` 增加：

```yaml
agentscope:
  enabled: true
  mode: equivalent_mas   # existing: off|equivalent_mas|trace_only; add agentic_mas

agentic_mas:
  enabled: false
  max_rounds: 3
  max_actions_per_round: 2
  min_new_evidence: 1
  stop_on_no_novel_chunks: true
  prompt_version: agentic_v1
  workflows:
    missing_info:
      enabled: true
    wrong_answer_risk:
      enabled: true
    not_found_recovery:
      enabled: true
    uncertainty_conflict:
      enabled: true
  retrieval_actions:
    allow_slot_targeted: true
    allow_alias_retrieval: true
    allow_layer_expansion: true
    allow_source_specific: true
    allow_contrastive: true
    allow_disambiguation: true
  trace:
    write_agentic_trace: true
    write_round_states: true
```

CLI 增加轻量 flags，用于实验覆盖配置：

```text
--agentic-mas
--agentic-max-rounds N
--disable-missing-info
--disable-wrong-answer-risk
--disable-not-found-recovery
--disable-uncertainty-conflict
```

所有 switch 的默认值应以 yaml 为准；CLI 只覆盖运行时 config。

---

## 3. Schema 设计

在 `src/nested_doc_rag/agent/mas/schemas.py` 增加以下 dataclass/Enum。避免引入复杂依赖，保持 JSON 可序列化。

```python
class EvidenceStateKind(str, Enum):
    SUFFICIENT = "sufficient"
    MISSING_INFO = "missing_info"
    WRONG_ANSWER_RISK = "wrong_answer_risk"
    NOT_FOUND_RECOVERY = "not_found_recovery"
    UNCERTAINTY_CONFLICT = "uncertainty_conflict"
    UNRESOLVED = "unresolved"

class FailureMode(str, Enum):
    SLOT_MISSING = "slot_missing"
    GRANULARITY_GAP = "granularity_gap"
    ENTITY_MISMATCH = "entity_mismatch"
    ATTRIBUTE_MISMATCH = "attribute_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    FORMAT_GAP = "format_gap"
    TEMPORAL_GAP = "temporal_gap"
    SOURCE_CONFLICT = "source_conflict"
    CANDIDATE_CONFLICT = "candidate_conflict"
    EVIDENCE_ABSENCE = "evidence_absence"
    WEAK_GROUNDING = "weak_grounding"

class ActionType(str, Enum):
    STOP = "stop"
    SLOT_TARGETED_RETRIEVAL = "slot_targeted_retrieval"
    ALIAS_RETRIEVAL = "alias_retrieval"
    LAYER_EXPANSION = "layer_expansion"
    SOURCE_SPECIFIC_RETRIEVAL = "source_specific_retrieval"
    CONTRASTIVE_RETRIEVAL = "contrastive_retrieval"
    DISAMBIGUATION_RETRIEVAL = "disambiguation_retrieval"
    MARK_UNRESOLVED = "mark_unresolved"
```

核心对象：

```python
@dataclass(frozen=True)
class EvidenceCandidate:
    value: str
    supporting_chunk_ids: list[str]
    refuting_chunk_ids: list[str]
    scope: str | None = None
    reason: str | None = None

@dataclass(frozen=True)
class EvidenceDiagnosis:
    state_kind: EvidenceStateKind
    failure_modes: list[FailureMode]
    sufficiency: str
    missing_information_need: str | None
    candidate_answers: list[EvidenceCandidate]
    risk_reason: str | None
    recommended_next_actions: list["EvidenceAction"]

@dataclass(frozen=True)
class EvidenceAction:
    action_type: ActionType
    query_text: str
    target_slot: str | None = None
    target_layer: str | None = None
    source_type_preference: str | None = None
    purpose: str | None = None
    semantic_invariant: dict[str, Any] | None = None
    expected_gain_type: str | None = None

@dataclass
class AgenticMASState:
    item: dict[str, Any]
    base_query: str
    current_query: str
    round_index: int
    evidence: list[dict[str, Any]]
    vector_hits: list[dict[str, Any]]
    generated: dict[str, Any] | None
    prediction: FieldPrediction | None
    diagnosis: EvidenceDiagnosis | None
    actions_taken: list[EvidenceAction]
    stopped_reason: str | None = None
```

扩展 `AnswerArbitrationOutput`：

```python
@dataclass(frozen=True)
class AnswerArbitrationOutput:
    generated: dict[str, Any]
    prediction: FieldPrediction
    generation_latency_ms: float
    diagnosis: EvidenceDiagnosis | None = None
```

---

## 4. 四个 agent 职责

### 4.1 AnswerArbiter

修改现有 `AnswerArbitrationRole`，让它在 `agentic_v1` prompt 下输出额外字段 `evidence_diagnosis`。不要改变 `answer_status/answer_value/source_chunk_ids` 的原语义。新增字段可以放在 `generated["evidence_diagnosis"]`，再由 `parse_evidence_diagnosis(...)` 转成 dataclass。

输出职责：

```text
当前答案是什么；
是否证据充分；
若不足，属于 missing_info / not_found_recovery / uncertainty_conflict；
若 answered，是否存在 wrong_answer_risk；
列出候选答案、支持证据、反证证据、缺失信息需求；
给出 recommended_next_actions，但最终是否执行由 router 和 switches 决定。
```

### 4.2 QueryReplanner

新增 `QueryReplannerRole`。输入 `AgenticMASState + EvidenceDiagnosis`，输出 `list[EvidenceAction]`。它不直接检索，只生成结构化动作。

典型动作：

```text
missing_info -> SLOT_TARGETED_RETRIEVAL
not_found_recovery -> ALIAS_RETRIEVAL / LAYER_EXPANSION / SOURCE_SPECIFIC_RETRIEVAL
uncertainty_conflict -> DISAMBIGUATION_RETRIEVAL
```

必须保留 `semantic_invariant`：实体、属性、粒度、单位/格式，不允许把原字段问题改成另一个问题。

### 4.3 EvidenceRetriever

保留并扩展现有 `EvidenceRetrievalRole`：新增 `run_action(action: EvidenceAction)`。它把 action 的 `query_text` 送入 `runner.retrieve(...)`，再 attach parent payload。合并 evidence 时按 `chunk_id` 去重，记录 `new_chunk_ids` 与 `novelty_count`。

### 4.4 Skeptic

新增 `SkepticRole`。输入当前候选答案或 answered prediction，输出反证/挑战动作。

典型动作：

```text
wrong_answer_risk -> CONTRASTIVE_RETRIEVAL
uncertainty_conflict -> 对每个候选答案生成挑战 query
```

Skeptic 的目标不是找更多支持证据，而是找能推翻当前答案的同实体、同属性、同粒度证据。

---

## 5. Router：四类状态与 workflow

Router 不是第五个 agent，而是 `Step15MASController` 内的状态机/策略函数：

```python
def select_actions(state: AgenticMASState, cfg: AgenticMASConfig) -> list[EvidenceAction]:
    diagnosis = state.diagnosis
    if diagnosis is None:
        return [EvidenceAction(ActionType.STOP, query_text=state.current_query, purpose="no diagnosis")]
    if diagnosis.state_kind == SUFFICIENT:
        return [STOP]
    if diagnosis.state_kind == MISSING_INFO and cfg.workflows.missing_info.enabled:
        return query_replanner.run_missing_info(state)
    if diagnosis.state_kind == WRONG_ANSWER_RISK and cfg.workflows.wrong_answer_risk.enabled:
        return skeptic.run_wrong_answer_risk(state)
    if diagnosis.state_kind == NOT_FOUND_RECOVERY and cfg.workflows.not_found_recovery.enabled:
        return query_replanner.run_not_found_recovery(state)
    if diagnosis.state_kind == UNCERTAINTY_CONFLICT and cfg.workflows.uncertainty_conflict.enabled:
        return query_replanner.run_disambiguation(state) + skeptic.run_candidate_challenges(state)
    return [MARK_UNRESOLVED]
```

四类 workflow：

```text
A. missing_info:
Arbiter -> Replanner(slot-targeted) -> Retriever -> Arbiter

B. wrong_answer_risk:
Arbiter -> Skeptic(contrastive) -> Retriever -> Arbiter

C. not_found_recovery:
Arbiter -> Replanner(alias/layer/source-specific) -> Retriever -> Arbiter

D. uncertainty_conflict:
Arbiter -> Replanner(disambiguation) + Skeptic(candidate challenge) -> Retriever -> Arbiter
```

停止条件：

```text
1. Arbiter 输出 sufficient；
2. 对应 workflow switch 关闭；
3. action 为 STOP 或 MARK_UNRESOLVED；
4. 达到 max_rounds；
5. 本轮没有 novel chunks 且 stop_on_no_novel_chunks=true。
```

`max_rounds` 是计算预算，不作为方法核心结论；实验报告应同时给出平均 round 和平均 retrieval cost。

---

## 6. Controller 改造

在 `src/nested_doc_rag/agent/mas/controller.py`：

1. 初始化新增 roles：
   - `self.query_replanner = QueryReplannerRole(runner)`
   - `self.skeptic = SkepticRole(runner)`
2. 新增方法：
   - `run_query_replanner(...)`
   - `run_skeptic(...)`
   - `run_evidence_retrieval_action(...)`
   - `process_item_agentic(...)`
3. 保持 `process_item(...)` 的旧逻辑不变；仅当 `mode == "agentic_mas"` 时进入新循环。

伪代码：

```python
query_plan = run_query_planner(item)
retrieval = run_evidence_retrieval(item, query_plan.query_text)
state = AgenticMASState(...)

for round_idx in range(cfg.max_rounds):
    arbitration = run_answer_arbitration(item, state.current_query, state.evidence, slot_schema=slot_schema)
    state.generated = arbitration.generated
    state.prediction = arbitration.prediction
    state.diagnosis = arbitration.diagnosis
    trace_round_state(state)

    actions = select_actions(state, cfg)
    actions = limit_actions(actions, cfg.max_actions_per_round)
    if should_stop(actions, state, cfg):
        break

    for action in actions:
        retrieval = run_evidence_retrieval_action(item, action)
        state.evidence = merge_hits(state.evidence, retrieval.top_hits)
        state.vector_hits = merge_hits(state.vector_hits, retrieval.vector_hits)
        state.actions_taken.append(action)
        state.current_query = action.query_text

# final overlay only after loop
final_overlay = run_overlay_control(item, state.generated, state.prediction, state.evidence)
return Step15FieldResult(...final state...)
```

---

## 7. Runner 接入

在 `Step15AgentRunner.process_item` 增加：

```python
if self.mas_mode == "agentic_mas" and self.mas_controller is not None:
    return self.mas_controller.process_item_agentic(item, config=self.agentic_mas_config)
```

`_process_item_equivalent_mas` 保持不动，作为 baseline。

在 runner 初始化中解析 `agentic_mas` 配置。若 `agentic_mas.enabled=true`，但 `agentscope.mode` 不是 `agentic_mas`，以 `agentscope.mode` 为准，不隐式切换，避免实验污染。

---

## 8. Prompt 与 JSON 输出

在 `step15_engine.py` 新增 `prompt_version="agentic_v1"`。保留原 `build_qdrant_answer_messages(...)`，但 `agentic_v1` 的 schema 追加：

```json
"evidence_diagnosis": {
  "state_kind": "sufficient|missing_info|wrong_answer_risk|not_found_recovery|uncertainty_conflict|unresolved",
  "failure_modes": ["slot_missing", "granularity_gap", "entity_mismatch", "scope_mismatch", "source_conflict", "evidence_absence", "weak_grounding"],
  "sufficiency": "sufficient|insufficient|contradictory|risky",
  "missing_information_need": "string|null",
  "candidate_answers": [
    {"value": "string", "supporting_chunk_ids": [], "refuting_chunk_ids": [], "scope": "string|null", "reason": "string|null"}
  ],
  "risk_reason": "string|null",
  "recommended_next_actions": [
    {"action_type": "...", "query_text": "...", "target_slot": "...", "purpose": "...", "semantic_invariant": {}}
  ]
}
```

约束：

```text
- evidence_diagnosis 只能基于 retrieved_chunks。
- answered 也可以输出 wrong_answer_risk，但必须说明风险来自 evidence alignment，而不是常识。
- not_found_recovery 表示当前检索视角没找到，不等于语料中不存在。
- uncertainty_conflict 必须显式列 candidate_answers。
```

---

## 9. 消融实验设计

建议实验矩阵：

```text
baseline: equivalent_mas
full: agentic_mas + all workflows
w/o missing_info
w/o wrong_answer_risk
w/o not_found_recovery
w/o uncertainty_conflict
only missing_info
only wrong_answer_risk
only not_found_recovery
only uncertainty_conflict
fixed_second_retrieval baseline
random_rewrite baseline
```

新增指标：

```text
answer accuracy / judge score
not_found_recovery_rate
partial_to_answered_rate
conflict_resolution_rate
answer_revision_rate
wrong_answer_risk_trigger_rate
average_rounds
average_retrieval_actions
novel_chunk_rate
writeback_allowed_rate
review_required_rate
```

每个 `review_items` 和 `trace` 应能追溯：初始状态、触发 workflow、动作 query、新证据、最终答案是否变化。

---

## 10. 测试要求

新增测试文件：

```text
tests/agent/test_agentic_mas_schemas.py
tests/agent/test_agentic_router.py
tests/agent/test_agentic_workflows.py
tests/agent/test_agentic_trace.py
```

测试必须使用 fake retriever / fake answer caller，不依赖真实服务。

必测场景：

```text
1. all switches off => 等价于一轮 arbitration 后停止。
2. missing_info enabled => partial_clue 触发 slot-targeted retrieval。
3. not_found_recovery enabled => not_found 触发 alias/layer action。
4. wrong_answer_risk enabled => answered+risk 触发 contrastive retrieval。
5. uncertainty_conflict enabled => conflict_unresolved 触发 disambiguation + skeptic。
6. no novel chunks => 停止且 trace 写 stopped_reason。
7. final overlay 使用最终轮 prediction，不使用初始 prediction。
```

---

## 11. 完成标准

1. `pytest` 通过。
2. `run-step15-agent --agentic-mas` 能生成现有 artifacts，并额外生成：
   - `agentic_mas_trace.jsonl`
   - `agentic_round_states.jsonl`
   - `agentic_summary.json`
3. `equivalent_mas` 行为不回归。
4. 关闭任一 workflow switch 后，trace 中不再出现对应 action type。
5. 所有新增 JSON 字段都有容错解析；LLM 缺失 `evidence_diagnosis` 时退化为 deterministic diagnosis。
6. 文档补充 `docs/agentic_evidence_replanning_mas_design.md`，README 只加一段链接，不展开长设计。
