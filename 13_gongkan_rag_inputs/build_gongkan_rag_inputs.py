from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path("/Users/mao/projects/datacenter")
STEP12_DIR = PROJECT_ROOT / "artifacts/12_gongkan_form_analysis"
DEFAULT_OUT_DIR = PROJECT_ROOT / "artifacts/13_gongkan_rag_inputs"

DEFAULT_LLM_URL = "http://111.19.156.30:8006/v1/chat/completions"
DEFAULT_LLM_MODEL = "deepseek-v4-flash"

DEFAULT_VECTOR_TOP_K = 30
DEFAULT_RERANK_TOP_N = 8


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def display_text(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if limit and len(text) > limit:
        return text[: limit - 1] + "..."
    return text


def md(value: Any, limit: int = 120) -> str:
    return display_text(value, limit).replace("|", "\\|")


def stable_id(*parts: Any) -> str:
    text = "|".join(display_text(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def prompt_hash(prompt: str, model: str) -> str:
    return hashlib.sha256((model + "\n" + prompt).encode("utf-8")).hexdigest()[:16]


def sheet_key(record: dict[str, Any]) -> tuple[str, int]:
    return (record["file_id"], int(record["sheet_index"]))


def sample_items_for_sheet(items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in items:
        selected.append(
            {
                "form_item_id": item["form_item_id"],
                "row_index": item["row_index"],
                "target_cell": item.get("target_cell"),
                "category_path": item.get("category_path") or [],
                "question_text": item.get("question_text"),
                "instruction_text": item.get("instruction_text"),
                "answer_example": item.get("answer_example"),
                "existing_value": item.get("existing_value"),
                "status_value": item.get("status_value"),
                "current_info": item.get("current_info"),
                "needs_evidence": item.get("needs_evidence"),
                "rag_return_mode": (item.get("rag_return_format") or {}).get("mode"),
            }
        )
        if len(selected) >= limit:
            break
    return selected


def deterministic_template(
    profile: dict[str, Any],
    agent_hint: dict[str, Any],
) -> dict[str, Any]:
    mode = ((agent_hint.get("rag_return_format") or {}).get("mode") or profile["rag_return_format"]["mode"])
    if profile["sheet_kind"] in {"evidence_or_layout_sheet", "non_fill_grid"}:
        mode = "evidence_or_layout_reference"
    elif mode == "evidence_or_layout_reference":
        mode = "cell_answer"
    include_fields = ["target_datacenter_id", "category_path", "question_text"]
    auxiliary_fields = ["instruction_text"]
    if profile.get("sample_answer_columns"):
        auxiliary_fields.append("answer_example")
    if profile["sheet_kind"] in {"assessment_matrix", "filtered_assessment_findings"}:
        include_fields.extend(["status_value", "current_info"])
    if profile["sheet_kind"] == "risk_register":
        include_fields.extend(["existing_value", "current_info"])
    return {
        "template_source": "deterministic",
        "sheet_kind": profile["sheet_kind"],
        "query_goal": "为每个工勘项构造 RAG 检索问题；只检索证据，不生成答案。",
        "include_fields": include_fields,
        "auxiliary_fields": auxiliary_fields,
        "answer_sample_policy": "format_hint_only; must_not_copy_without_rag_support",
        "existing_value_policy": "form_value_hint_only; final answer must be supported by retrieved chunks",
        "evidence_policy": {
            "needs_evidence": bool(profile.get("needs_evidence")),
            "rule": "如果 item.needs_evidence=true，答案必须优先返回 evidence_attachments；图片只作为佐证，不 OCR。",
        },
        "retrieval_policy": {
            "namespace_filter": "target_namespace + global",
            "layers": ["fact", "evidence"],
            "vector_top_k": DEFAULT_VECTOR_TOP_K,
            "rerank_top_n": DEFAULT_RERANK_TOP_N,
        },
        "rag_return_mode": mode,
        "agent_notes": agent_hint.get("shape_summary") or "",
    }


def build_agent_prompt(
    profile: dict[str, Any],
    agent_hint: dict[str, Any],
    sample_items: list[dict[str, Any]],
    contract: dict[str, Any],
) -> str:
    schema = {
        "sheet_kind": "string",
        "query_goal": "string",
        "include_fields": ["target_datacenter_id", "category_path", "question_text"],
        "auxiliary_fields": ["instruction_text", "answer_example"],
        "answer_sample_policy": "format_hint_only | semantic_hint_but_must_verify | ignore",
        "existing_value_policy": "form_value_hint_only | ignore | verify_existing_value",
        "evidence_policy": {
            "needs_evidence": False,
            "rule": "string",
        },
        "retrieval_policy": {
            "namespace_filter": "target_namespace + global",
            "layers": ["fact", "evidence"],
            "vector_top_k": 30,
            "rerank_top_n": 8,
        },
        "rag_return_mode": "cell_answer | status_with_current_info | finding_row | risk_row | evidence_or_layout_reference",
        "agent_notes": "string",
    }
    source = {
        "file_name": profile["file_name"],
        "sheet_name": profile["sheet_name"],
        "deterministic_profile": profile,
        "sheet_agent_hint": agent_hint,
        "sample_form_items": sample_items,
        "allowed_return_modes": list(contract["modes"].keys()),
    }
    return (
        "你是工勘单 RAG 输入结构设计智能体。请只输出严格 JSON，不要 Markdown，不要解释。\n"
        "你的任务是根据 sheet 结构和样例行，决定后续每个填报项应该如何构造 RAG 检索输入。\n"
        "你不能生成工勘答案，不能补充业务事实，不能把示例当作最终答案。\n\n"
        "输出 JSON 必须符合这个 schema：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "硬性规则：\n"
        "1. answer_sample 只能作为格式参考或检索提示，最终答案必须由 RAG 命中原文支持。\n"
        "2. existing_value 只是表内已有值，不能当作知识库证据。\n"
        "3. 如果需要证明材料，RAG 输入必须要求返回 evidence_attachments；图片不 OCR。\n"
        "4. rag_return_mode 必须是允许模式之一；无填报目标的照片/平面图页使用 evidence_or_layout_reference。\n"
        "5. include_fields/auxiliary_fields 只能写字段名，不写答案。\n\n"
        f"上下文：\n{json.dumps(source, ensure_ascii=False, indent=2)}\n"
    )


def call_llm_json(
    *,
    url: str,
    model: str,
    api_key: str,
    prompt: str,
    timeout: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "你只设计 RAG 输入结构，必须输出严格 JSON。"},
            {"role": "user", "content": prompt},
        ],
    }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [
                "curl",
                "--noproxy",
                "*",
                "-sS",
                "--max-time",
                str(timeout),
                "-X",
                "POST",
                url,
                "-H",
                "Content-Type: application/json",
                "-H",
                f"Authorization: Bearer {api_key}",
                "-d",
                f"@{tmp_path}",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed: {proc.stderr.strip() or proc.stdout.strip()}")
    response = json.loads(proc.stdout)
    content = response["choices"][0]["message"]["content"].strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    if not content.startswith("{"):
        match = re.search(r"\{[\s\S]*\}", content)
        if match:
            content = match.group(0)
    return json.loads(content)


def normalize_template(template: dict[str, Any], fallback: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(fallback)
    normalized.update({key: value for key, value in template.items() if value not in (None, "", [])})
    mode = normalized.get("rag_return_mode")
    allowed_template_modes = set(contract["modes"]) | {"evidence_or_layout_reference"}
    if fallback.get("rag_return_mode") == "evidence_or_layout_reference":
        normalized["rag_return_mode"] = "evidence_or_layout_reference"
    elif mode not in allowed_template_modes:
        normalized["rag_return_mode"] = fallback["rag_return_mode"]
    retrieval_policy = dict(fallback.get("retrieval_policy") or {})
    retrieval_policy.update(normalized.get("retrieval_policy") or {})
    retrieval_policy["layers"] = [layer for layer in retrieval_policy.get("layers", ["fact", "evidence"]) if layer in {"fact", "evidence", "meta", "template"}]
    retrieval_policy["vector_top_k"] = int(retrieval_policy.get("vector_top_k") or DEFAULT_VECTOR_TOP_K)
    retrieval_policy["rerank_top_n"] = int(retrieval_policy.get("rerank_top_n") or DEFAULT_RERANK_TOP_N)
    normalized["retrieval_policy"] = retrieval_policy
    normalized["template_source"] = template.get("template_source") or "deepseek"
    return normalized


def build_templates(
    profiles: list[dict[str, Any]],
    sheet_hints: list[dict[str, Any]],
    items: list[dict[str, Any]],
    contract: dict[str, Any],
    out_dir: Path,
    *,
    use_llm: bool,
    llm_url: str,
    llm_model: str,
    api_key_env: str,
    timeout: int,
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    hints_by_sheet = {sheet_key(hint): hint["agent_hint"] for hint in sheet_hints}
    items_by_sheet: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        items_by_sheet[sheet_key(item)].append(item)
    api_key = os.environ.get(api_key_env)
    if use_llm and not api_key:
        raise RuntimeError(f"missing API key env: {api_key_env}")

    templates: list[dict[str, Any]] = []
    for profile in profiles:
        key = sheet_key(profile)
        agent_hint = hints_by_sheet.get(key, {})
        fallback = deterministic_template(profile, agent_hint)
        sample_items = sample_items_for_sheet(items_by_sheet.get(key, []))
        if use_llm:
            prompt = build_agent_prompt(profile, agent_hint, sample_items, contract)
            cache_key = prompt_hash(prompt, llm_model)
            cache_path = cache_dir / f"{cache_key}.json"
            if cache_path.exists():
                model_template = read_json(cache_path)
            else:
                model_template = call_llm_json(
                    url=llm_url,
                    model=llm_model,
                    api_key=api_key or "",
                    prompt=prompt,
                    timeout=timeout,
                )
                write_json(cache_path, model_template)
            template = normalize_template(model_template, fallback, contract)
            source = "deepseek"
        else:
            cache_key = stable_id(profile["file_id"], profile["sheet_index"], "deterministic")
            template = fallback
            source = "deterministic"
        templates.append(
            {
                "file_id": profile["file_id"],
                "file_name": profile["file_name"],
                "sheet_index": profile["sheet_index"],
                "sheet_name": profile["sheet_name"],
                "template_source": source,
                "prompt_hash": cache_key,
                "rag_input_template": template,
            }
        )
    return templates


def infer_expected_answer_type(item: dict[str, Any], mode: str) -> str:
    text = " ".join(
        display_text(value)
        for value in [
            item.get("question_text"),
            item.get("instruction_text"),
            item.get("answer_example"),
            item.get("current_info"),
        ]
    )
    if mode in {"status_with_current_info", "finding_row"}:
        return "status_and_current_info"
    if mode == "risk_row":
        return "risk_fields"
    if any(token in text for token in ["是否", "能否", "有无", "具备"]):
        return "yes_no_or_short_status"
    if any(token in text for token in ["数量", "几台", "多少", "容量", "功率", "电流", "带宽", "时长", "温度", "湿度"]):
        return "number_or_value_with_unit"
    if any(token in text for token in ["联系人", "电话", "邮箱"]):
        return "contact_text"
    if any(token in text for token in ["地址", "位置"]):
        return "location_text"
    if item.get("needs_evidence"):
        return "answer_with_evidence"
    return "short_text"


def build_retrieval_query(item: dict[str, Any], target_namespace: str, template: dict[str, Any]) -> str:
    mode = (item.get("rag_return_format") or {}).get("mode") or template.get("rag_return_mode")
    category = " / ".join(item.get("category_path") or [])
    parts = [f"目标机房：{target_namespace}"]
    if mode == "status_with_current_info":
        parts.append("任务：判断评估项满足情况，并找到可写入机房现状的原文。")
        parts.append(f"评估内容：{item.get('question_text')}")
    elif mode == "finding_row":
        parts.append("任务：生成不满足或部分满足清单行，需要类别、评估内容、满足情况和机房现状。")
        parts.append(f"评估内容：{item.get('question_text')}")
    elif mode == "risk_row":
        parts.append("任务：查找相关风险登记信息，需要风险描述、影响和解决方案。")
        parts.append(f"风险项：{item.get('question_text') or item.get('existing_value') or item.get('current_info')}")
    else:
        parts.append("任务：为工勘单目标单元格查找可支撑的短答案。")
        parts.append(f"填报项：{item.get('question_text')}")
    if category:
        parts.append(f"类别：{category}")
    if item.get("instruction_text"):
        parts.append(f"填写说明/备注：{item['instruction_text']}")
    if item.get("answer_example"):
        parts.append(f"答复示例仅作格式参考，不可直接复制：{item['answer_example']}")
    if item.get("existing_value"):
        parts.append(f"表内已有值仅作线索，最终必须由知识库证据支持：{item['existing_value']}")
    if item.get("needs_evidence"):
        parts.append("该项需要证明材料或图片佐证；如果命中证据附件，请返回 evidence_attachments。")
    parts.append("只能使用检索到的原文和证据；找不到就返回未找到。")
    return "。".join(display_text(part).rstrip("。") for part in parts if display_text(part)) + "。"


def output_schema_for_mode(mode: str, contract: dict[str, Any]) -> dict[str, Any]:
    mode_contract = contract["modes"][mode]
    schema: dict[str, Any] = {
        "form_item_id": "string",
        "mode": mode,
        "confidence": "number",
        "source_chunks": "array",
        "evidence_attachments": "array",
        "missing_fields": "array",
        "notes": "string",
    }
    schema.update(mode_contract.get("payload") or {})
    return schema


def build_answer_prompt(request: dict[str, Any]) -> str:
    schema = request["answer_contract"]["output_schema"]
    return (
        "你是工勘单 RAG 答案整理器。你只能使用输入的 retrieved_chunks 和 evidence_attachments，不能使用常识或猜测。\n"
        "如果 retrieved_chunks 中没有足够证据，必须把缺失项写入 missing_fields，并将可写答案置为“未找到”或空值。\n"
        "答复示例只能作为格式参考，不能作为事实来源。图片不 OCR，只作为 evidence_attachments 佐证。\n\n"
        f"目标工勘项：\n{json.dumps(request['form_item'], ensure_ascii=False, indent=2)}\n\n"
        f"RAG 查询：{request['retrieval']['query_text']}\n\n"
        "检索结果占位：\n{retrieved_chunks_json}\n\n"
        "证据附件占位：\n{evidence_attachments_json}\n\n"
        "请只输出严格 JSON，schema 如下：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n"
    )


def build_request(
    item: dict[str, Any],
    template: dict[str, Any],
    contract: dict[str, Any],
    target_namespace: str,
    include_global: bool,
) -> dict[str, Any]:
    item_mode = (item.get("rag_return_format") or {}).get("mode")
    mode = item_mode if item_mode in contract["modes"] else template.get("rag_return_mode")
    if mode not in contract["modes"]:
        mode = "cell_answer"
    namespace_filter = [target_namespace]
    if include_global and target_namespace != "global":
        namespace_filter.append("global")
    retrieval_policy = template.get("retrieval_policy") or {}
    query_text = build_retrieval_query(item, target_namespace, template)
    request = {
        "rag_request_id": "gkrag_" + stable_id(item["form_item_id"], target_namespace, mode),
        "target_namespace": target_namespace,
        "namespace_filter": namespace_filter,
        "mode": mode,
        "task_action": {
            "empty": "fill_empty",
            "placeholder": "fill_placeholder",
            "has_value": "verify_or_rewrite_existing",
        }.get(item.get("answer_state"), "fill_or_verify"),
        "form_item": {
            "form_item_id": item["form_item_id"],
            "file_name": item["file_name"],
            "relative_path": item.get("relative_path"),
            "sheet_name": item["sheet_name"],
            "row_index": item["row_index"],
            "target_cell": item.get("target_cell"),
            "category_path": item.get("category_path") or [],
            "question_text": item.get("question_text"),
            "instruction_text": item.get("instruction_text"),
            "answer_example": item.get("answer_example"),
            "answer_sample_policy": template.get("answer_sample_policy"),
            "existing_value": item.get("existing_value"),
            "existing_value_policy": template.get("existing_value_policy"),
            "status_value": item.get("status_value"),
            "current_info": item.get("current_info"),
            "needs_evidence": bool(item.get("needs_evidence")),
            "evidence_reason": item.get("evidence_reason"),
        },
        "query_components": {
            "primary_question": item.get("question_text") or item.get("existing_value") or item.get("current_info"),
            "category_path": item.get("category_path") or [],
            "auxiliary_context": {
                "instruction_text": item.get("instruction_text"),
                "answer_example_format_only": item.get("answer_example"),
                "existing_value_hint_only": item.get("existing_value"),
                "status_value": item.get("status_value"),
                "current_info": item.get("current_info"),
            },
            "expected_answer_type": infer_expected_answer_type(item, mode),
            "evidence_required": bool(item.get("needs_evidence")),
        },
        "retrieval": {
            "query_text": query_text,
            "vector_top_k": int(retrieval_policy.get("vector_top_k") or DEFAULT_VECTOR_TOP_K),
            "rerank_top_n": int(retrieval_policy.get("rerank_top_n") or DEFAULT_RERANK_TOP_N),
            "layers": retrieval_policy.get("layers") or ["fact", "evidence"],
            "namespace_filter": namespace_filter,
            "prefer_policy": [
                "prefer same namespace over global when scores are close",
                "prefer table_procedure for procedure questions",
                "attach evidence_attachments when evidence_required=true or chunk has image flags",
            ],
        },
        "answer_contract": {
            "contract_name": contract["contract_name"],
            "mode": mode,
            "required_fields": contract["modes"][mode]["required_fields"],
            "output_schema": output_schema_for_mode(mode, contract),
            "hard_constraints": contract["principle"],
        },
        "agent_template": {
            "template_source": template.get("template_source"),
            "sheet_kind": template.get("sheet_kind"),
            "include_fields": template.get("include_fields"),
            "auxiliary_fields": template.get("auxiliary_fields"),
            "evidence_policy": template.get("evidence_policy"),
            "agent_notes": template.get("agent_notes"),
        },
    }
    request["answer_prompt_template"] = build_answer_prompt(request)
    return request


def write_visualization(out_dir: Path, requests: list[dict[str, Any]], templates: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# Step 13 工勘单 RAG 问题输入结构\n")
    lines.append("本步骤只构造 RAG 输入，不执行检索，不生成工勘答案。\n")
    lines.append("## 总览\n")
    lines.append(f"- RAG 请求数：**{summary['rag_request_count']}**")
    lines.append(f"- 目标分库：`{summary['target_namespace']}`")
    lines.append(f"- Sheet 模板数：**{summary['template_count']}**")
    lines.append(f"- 需要证据附件的请求：**{summary['requests_needing_evidence']}**\n")

    lines.append("## 请求类型\n")
    lines.append("| mode | count |")
    lines.append("|---|---:|")
    for mode, count in sorted(summary["counts_by_mode"].items()):
        lines.append(f"| `{mode}` | {count} |")

    lines.append("\n## Agent 模板样例\n")
    lines.append("| file | sheet | source | mode | include_fields | auxiliary_fields | notes |")
    lines.append("|---|---|---|---|---|---|---|")
    for template_record in templates[:20]:
        template = template_record["rag_input_template"]
        lines.append(
            f"| {md(template_record['file_name'], 30)} | {md(template_record['sheet_name'], 24)} | "
            f"`{template_record['template_source']}` | `{template.get('rag_return_mode')}` | "
            f"{md(template.get('include_fields'), 80)} | {md(template.get('auxiliary_fields'), 80)} | "
            f"{md(template.get('agent_notes'), 90)} |"
        )

    lines.append("\n## RAG 请求样例\n")
    lines.append("| # | mode | action | target | evidence | query_text | output fields |")
    lines.append("|---:|---|---|---|---|---|---|")
    for index, request in enumerate(requests[:40], 1):
        form_item = request["form_item"]
        lines.append(
            f"| {index} | `{request['mode']}` | `{request['task_action']}` | "
            f"{md(form_item['file_name'], 24)} / {md(form_item['sheet_name'], 18)} / `{form_item.get('target_cell') or ''}` | "
            f"{'yes' if form_item.get('needs_evidence') else 'no'} | "
            f"{md(request['retrieval']['query_text'], 180)} | "
            f"{md(list(request['answer_contract']['output_schema'].keys()), 120)} |"
        )

    (out_dir / "visualization.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(
    step12_dir: Path = STEP12_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    target_namespace: str = "xixian_6",
    include_global: bool = True,
    use_llm: bool = False,
    llm_url: str = DEFAULT_LLM_URL,
    llm_model: str = DEFAULT_LLM_MODEL,
    api_key_env: str = "DEEPSEEK_API_KEY",
    timeout: int = 120,
    limit: int | None = None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    profiles = read_jsonl(step12_dir / "sheet_profiles.jsonl")
    sheet_hints = read_jsonl(step12_dir / "agent_sheet_hints.jsonl")
    items = read_jsonl(step12_dir / "form_items.jsonl")
    contract = read_json(step12_dir / "rag_return_contract.json")
    if limit is not None:
        items = items[:limit]

    templates = build_templates(
        profiles,
        sheet_hints,
        items,
        contract,
        out_dir,
        use_llm=use_llm,
        llm_url=llm_url,
        llm_model=llm_model,
        api_key_env=api_key_env,
        timeout=timeout,
    )
    template_by_sheet = {sheet_key(record): record["rag_input_template"] for record in templates}
    requests = [
        build_request(
            item,
            template_by_sheet[sheet_key(item)],
            contract,
            target_namespace=target_namespace,
            include_global=include_global,
        )
        for item in items
    ]

    summary = {
        "target_namespace": target_namespace,
        "include_global": include_global,
        "template_count": len(templates),
        "rag_request_count": len(requests),
        "requests_needing_evidence": sum(1 for request in requests if request["form_item"]["needs_evidence"]),
        "agent_backend": "deepseek" if use_llm else "deterministic",
        "counts_by_mode": dict(Counter(request["mode"] for request in requests)),
        "counts_by_task_action": dict(Counter(request["task_action"] for request in requests)),
        "counts_by_file": dict(Counter(request["form_item"]["file_name"] for request in requests)),
        "principle": "Agent 只参与 RAG 输入结构设计；最终答案必须由检索原文和证据附件支持。",
    }

    write_jsonl(out_dir / "agent_rag_input_templates.jsonl", templates)
    write_jsonl(out_dir / "rag_question_inputs.jsonl", requests)
    write_json(out_dir / "summary.json", summary)
    write_visualization(out_dir, requests, templates, summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 13: build agent-guided RAG question input structures for gongkan form items.")
    parser.add_argument("--step12-dir", type=Path, default=STEP12_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--target-namespace", default="xixian_6")
    parser.add_argument("--no-global", action="store_true")
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--llm-url", default=DEFAULT_LLM_URL)
    parser.add_argument("--llm-model", default=DEFAULT_LLM_MODEL)
    parser.add_argument("--api-key-env", default="DEEPSEEK_API_KEY")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--limit", type=int, default=0, help="0 means all form items.")
    args = parser.parse_args()
    summary = run(
        step12_dir=args.step12_dir,
        out_dir=args.out_dir,
        target_namespace=args.target_namespace,
        include_global=not args.no_global,
        use_llm=args.use_llm,
        llm_url=args.llm_url,
        llm_model=args.llm_model,
        api_key_env=args.api_key_env,
        timeout=args.timeout,
        limit=None if args.limit <= 0 else args.limit,
    )
    print(
        f"built {summary['rag_request_count']} gongkan RAG input requests "
        f"for namespace={summary['target_namespace']} -> {args.out_dir}"
    )


if __name__ == "__main__":
    main()
