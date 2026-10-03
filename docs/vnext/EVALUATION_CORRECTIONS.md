# 评测金标与首轮对照修正

本次修复两处实际评测缺口。完整目标仍未完成，真实模型接口依旧断连。

## 未核准材料的计分资格

此前 `gold_verified=false` 仍可产生 Recall、Second-Round Evidence Gain；非可回答标注还能绕过部分 unsupported-answer 门控，缺少验证声明默认按 true 处理。最小反例及原版本快照保留在 `artifacts/vnext/phase5/gold-assessment-audit-01/`。

现在按显式 gold 声明及逐项标注完整性计分。未核准、heldout、候选或 partial-review 材料不进入质量分母，也不会得到 answer-supported 结论。未知项公开为 unknown；实际调用/轮数和独立核准的输入保护仍可观测。原30条固定 synthetic 领域规则数据及58个原生 locator 保持兼容，没有新增全141人工签审门槛。

## 严格首轮对照

此前 primary 配置仅保留 `target_structured_fact`，而实际 A3 首轮也查 `target_table_detail`；关闭 sufficiency 又改变首轮决策、hit 标注和回答 gate。因此旧 `ablation-stub-03` 的 Answer Gain 是耦合干预差值，不能单独归因给补查。原文件与报告未修改。

新 adapter 显式使用 `--primary-only` / `A3-primary-only-no-supplement-v1`。它保留同一 A3 配置、完整首轮候选与预算、首轮 sufficiency、hit 标注、回答 prompt 和 gate，仅禁止第二轮。历史 `src` 导出逐字不改；这是明确的实验控制适配。primary 子命令失败会保留失败阶段，并使整体实验失败。

## 验证

- 46 项 evaluator 回归、5 项实际历史类首轮对照测试通过；14 项 IO 注入探针证明控制流程和消息等价，不将它们当作 HTTP/Qdrant 或模型质量证明。
- 当前全量 Python：909 passed，0 failure/error/skip，75 compatibility warnings；Ruff及diff检查通过。
- 新 `ablation-stub-04`：12组实际历史方法运行、3组严格 primary 控制、12次离线评测；主运行12次及primary3次原生 validator均通过。
- 三对 primary 配置与各自主运行逐字相同；30字段仅一轮，首轮 query 与实际 Qdrant调用数均对应相同。原始run/primary文件在评分前后不变，旧003的382份运行/配置/authority文件 hash也未变。
- 上述运行使用显式协议替身，809次替身HTTP请求均200，不能证明真实模型准确率或补查收益。离线评分本身不调用模型。

Go/前端生产源码未变，沿用此前独立验证的523项 Go race leaf及39项前端结果；没有重跑或伪称本轮执行。原103个冻结文件已再次核对不变。完整命令、source/evidence hash与最新真实服务探测见 [结果](EVALUATION_CORRECTIONS_RESULTS.json)。

## 剩余真实验收

本次真实 embedding、rerank、chat POST 请求均约5秒后 `RemoteDisconnected`。TCP连接成功只说明端口可连接，不证明模型服务健康。请求已经尝试，provider usage/cost和权重指纹仍unknown。

接口恢复后依原顺序执行：新镜像与新storage identity的三对真实 fresh Docker，然后真实challenge/A0–A3及严格primary，最后旧141独立副本的blank/preserve live兼容运行。旧141 native材料仍为57条有限agent判定、84条候选、0正式gold；独立来源/状态核准是质量声明的前提，不新增全部141人工签审gate。
