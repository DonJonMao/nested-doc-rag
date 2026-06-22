from __future__ import annotations

import copy
import json
import os
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_ENV_PREFIX = "NESTED_DOC_RAG__"


def _default_layered_plan() -> list[dict[str, Any]]:
    return [
        {
            "layer_name": "target_main_fact",
            "description": "目标机房主知识库事实行，优先作为可填答案来源。",
            "namespaces": "target",
            "corpus_layers": ["fact", "evidence"],
            "source_types": ["main_excel_capability"],
            "vector_top_k": 16,
            "rerank_top_n": 5,
        },
        {
            "layer_name": "target_structured_detail",
            "description": "目标机房下钻出来的结构化表格内容，用于补充主表不足。",
            "namespaces": "target",
            "corpus_layers": ["fact"],
            "source_types": ["embedded_word_table"],
            "vector_top_k": 12,
            "rerank_top_n": 3,
        },
        {
            "layer_name": "target_raw_detail",
            "description": "目标机房下钻原文段落或表格行，只做补充线索。",
            "namespaces": "target",
            "corpus_layers": ["raw_text"],
            "source_types": ["embedded_raw_segment"],
            "vector_top_k": 12,
            "rerank_top_n": 3,
        },
        {
            "layer_name": "global_intro",
            "description": "全局介绍文档，用于解释园区级背景，不应无理由覆盖目标机房主表。",
            "namespaces": "global",
            "corpus_layers": ["intro_doc"],
            "source_types": ["intro_doc_paragraph", "intro_doc_table_row"],
            "vector_top_k": 10,
            "rerank_top_n": 3,
        },
        {
            "layer_name": "global_detail",
            "description": "全局下钻结构化或原文材料，只做低优先级补充。",
            "namespaces": "global",
            "corpus_layers": ["fact", "raw_text"],
            "source_types": ["embedded_word_table", "embedded_raw_segment"],
            "vector_top_k": 10,
            "rerank_top_n": 2,
        },
    ]


@dataclass(frozen=True)
class PathsConfig:
    project_root: Path
    data_dir: Path
    artifacts_dir: Path
    qdrant_path: Path
    temp_dir: Path


@dataclass(frozen=True)
class ServicesConfig:
    embedding_endpoint: str = "http://localhost:8001/v1/embeddings"
    embedding_model: str = "qwen3-embedding-8b"
    rerank_endpoint: str = "http://localhost:8002/rerank"
    rerank_model: str = ""
    chat_endpoint: str = "http://localhost:8006/v1/chat/completions"
    chat_model: str = "deepseek-v4-flash"
    chat_api_key_env: str = "DEEPSEEK_API_KEY"
    timeout_seconds: int = 120


@dataclass(frozen=True)
class QdrantConfig:
    collection_name: str = "datacenter_chunks_v1"
    url: str = ""
    api_key_env: str = "QDRANT_API_KEY"
    prefer_grpc: bool = False
    timeout: int = 60


@dataclass(frozen=True)
class RetrievalConfig:
    target_namespace: str = "xixian_4"
    global_namespace: str = "global"
    query_layers: list[str] = field(default_factory=lambda: ["fact", "evidence", "intro_doc", "raw_text", "meta"])
    plan: str = "layered"
    vector_top_k: int = 40
    rerank_top_n: int = 10
    layer_top_k: int = 8
    layer_rerank_top_n: int = 5
    max_reference_chunks: int = 5
    expand_parent_payload: bool = False
    parent_payload_max_chars: int = 300
    parent_payload_include_neighbors: bool = True
    parent_payload_neighbor_window: int = 1
    parent_payload_include_raw_parent_text: bool = True
    layered_plan: list[dict[str, Any]] = field(default_factory=_default_layered_plan)


@dataclass(frozen=True)
class GroundingConfig:
    evidence_strength_enabled: bool = True
    field_binding_enabled: bool = True
    min_strength_for_answered: str = "E3"
    min_strength_for_writeback: str = "E3"
    require_target_source_for_answered: bool = True
    global_intro_answer_allowed: bool = False
    downgrade_unsupported_answer_to_partial: bool = False
    write_grounding_trace: bool = True


@dataclass(frozen=True)
class EvaluationConfig:
    default_rows: list[int] = field(default_factory=lambda: [4, 5, 13, 16, 25, 26, 31, 36, 53, 117])
    timeout_seconds: int = 120
    resume: bool = False
    judge_model: str = "deepseek-v4-flash"
    metrics_output: Path = Path("artifacts/15_vector_store/base_cloud_closed_book_eval/summary.json")


@dataclass(frozen=True)
class ExcelConfig:
    template_path: Path = Path("data/工勘单/基地云机房信息调研表.xlsx")
    output_path: Path = Path("artifacts/excel_outputs/base_cloud_filled.xlsx")
    write_mode: str = "copy"
    write_comments: bool = True
    preserve_styles: bool = True


@dataclass(frozen=True)
class AgentConfig:
    max_repair_attempts: int = 2
    confidence_threshold: float = 0.72
    require_evidence_for_answer: bool = True
    human_review_threshold: float = 0.55
    retrieval_backend: str = "mini"
    generation_backend: str = "deterministic"
    enable_rerank: bool = False
    skip_generation_when_no_evidence: bool = True
    human_review_on_conflict: bool = True
    llm_json_repair: bool = True


@dataclass(frozen=True)
class AgentScopeConfig:
    enabled: bool = True
    mode: str = "equivalent_mas"


@dataclass(frozen=True)
class AppConfig:
    paths: PathsConfig
    services: ServicesConfig = field(default_factory=ServicesConfig)
    qdrant: QdrantConfig = field(default_factory=QdrantConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    grounding: GroundingConfig = field(default_factory=GroundingConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    excel: ExcelConfig = field(default_factory=ExcelConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    agentscope: AgentScopeConfig = field(default_factory=AgentScopeConfig)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


def discover_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "config" / "default.yaml").exists() or (candidate / "gongkan_agentic_rag_design.md").exists():
            return candidate
    return current


def default_config_path(project_root: Path | None = None) -> Path:
    return (project_root or discover_project_root()) / "config" / "default.yaml"


def code_defaults(project_root: Path | None = None) -> dict[str, Any]:
    root = (project_root or discover_project_root()).resolve()
    return {
        "paths": {
            "project_root": str(root),
            "data_dir": "data",
            "artifacts_dir": "artifacts",
            "qdrant_path": "artifacts/15_vector_store/qdrant",
            "temp_dir": "tmp",
        },
        "services": _serialize(ServicesConfig()),
        "qdrant": _serialize(QdrantConfig()),
        "retrieval": _serialize(RetrievalConfig()),
        "grounding": _serialize(GroundingConfig()),
        "evaluation": _serialize(EvaluationConfig()),
        "excel": _serialize(ExcelConfig()),
        "agent": _serialize(AgentConfig()),
        "agentscope": _serialize(AgentScopeConfig()),
    }


def load_app_config(
    config_path: str | Path | None = None,
    *,
    cli_overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    project_root: str | Path | None = None,
    default_config: str | Path | None = None,
) -> AppConfig:
    """Load app config with priority: CLI > env > YAML > code defaults."""

    root_hint = Path(project_root).expanduser().resolve() if project_root else discover_project_root()
    merged = code_defaults(root_hint)
    project_root_base = root_hint

    default_path = Path(default_config) if default_config else default_config_path(root_hint)
    if default_path.exists():
        default_data = load_yaml_file(default_path)
        _deep_merge(merged, default_data)
        if _has_nested_key(default_data, ("paths", "project_root")):
            project_root_base = default_path.parent

    if config_path:
        path = Path(config_path).expanduser()
        if not path.is_absolute():
            path = root_hint / path
        config_data = load_yaml_file(path)
        _deep_merge(merged, config_data)
        if _has_nested_key(config_data, ("paths", "project_root")):
            project_root_base = path.parent

    env_overrides = env_to_overrides(os.environ if env is None else env)
    _deep_merge(merged, env_overrides)

    if cli_overrides:
        _deep_merge(merged, _drop_none(copy.deepcopy(dict(cli_overrides))))

    return app_config_from_dict(merged, project_root_base=project_root_base)


load_config = load_app_config


def _normalize_step15_retrieval_plan(retrieval_data: Mapping[str, Any]) -> str:
    legacy_keys = [key for key in ("retrieval_plan", "retrieval_mode", "mode") if key in retrieval_data]
    legacy_values = {key: str(retrieval_data.get(key) or "").lower() for key in legacy_keys}
    if any(value == "hybrid" for value in legacy_values.values()):
        raise ValueError("Step15 production retrieval does not support hybrid mode")
    if legacy_keys:
        raw_value = retrieval_data.get("plan", retrieval_data.get(legacy_keys[0]))
        warnings.warn(
            "retrieval.mode/retrieval_mode/retrieval_plan are deprecated for Step15; using retrieval.plan=layered.",
            DeprecationWarning,
            stacklevel=3,
        )
    else:
        raw_value = retrieval_data.get("plan")
    value = str(raw_value or "layered")
    if value == "hybrid":
        raise ValueError("Step15 production retrieval does not support hybrid mode")
    if value != "layered":
        warnings.warn(
            f"Step15 production retrieval only supports layered; ignoring retrieval plan {value!r}.",
            DeprecationWarning,
            stacklevel=3,
        )
    return "layered"


def app_config_from_dict(data: Mapping[str, Any], *, project_root_base: Path | None = None) -> AppConfig:
    raw_paths = dict(data.get("paths") or {})
    base = project_root_base or discover_project_root()
    root = _resolve_project_root(raw_paths.get("project_root"), base)

    paths = PathsConfig(
        project_root=root,
        data_dir=_resolve_path(raw_paths.get("data_dir", "data"), root),
        artifacts_dir=_resolve_path(raw_paths.get("artifacts_dir", "artifacts"), root),
        qdrant_path=_resolve_path(raw_paths.get("qdrant_path", "artifacts/15_vector_store/qdrant"), root),
        temp_dir=_resolve_path(raw_paths.get("temp_dir", "tmp"), root),
    )
    services_data = _section(data, "services", ServicesConfig())
    services = ServicesConfig(
        embedding_endpoint=str(services_data.get("embedding_endpoint", "http://localhost:8001/v1/embeddings")),
        embedding_model=str(services_data.get("embedding_model", "qwen3-embedding-8b")),
        rerank_endpoint=str(services_data.get("rerank_endpoint", "http://localhost:8002/rerank")),
        rerank_model=str(services_data.get("rerank_model", "")),
        chat_endpoint=str(services_data.get("chat_endpoint", "http://localhost:8006/v1/chat/completions")),
        chat_model=str(services_data.get("chat_model", "deepseek-v4-flash")),
        chat_api_key_env=str(services_data.get("chat_api_key_env", "DEEPSEEK_API_KEY")),
        timeout_seconds=_as_int(services_data.get("timeout_seconds", 120)),
    )
    qdrant_data = _section(data, "qdrant", QdrantConfig())
    qdrant = QdrantConfig(
        collection_name=str(qdrant_data.get("collection_name", "datacenter_chunks_v1")),
        url=str(qdrant_data.get("url", "")),
        api_key_env=str(qdrant_data.get("api_key_env", "QDRANT_API_KEY")),
        prefer_grpc=_as_bool(qdrant_data.get("prefer_grpc", False)),
        timeout=_as_int(qdrant_data.get("timeout", 60)),
    )
    retrieval_data = _section(data, "retrieval", RetrievalConfig())
    retrieval = RetrievalConfig(
        target_namespace=str(retrieval_data.get("target_namespace", "xixian_4")),
        global_namespace=str(retrieval_data.get("global_namespace", "global")),
        query_layers=[str(item) for item in _as_list(retrieval_data.get("query_layers"))],
        plan=_normalize_step15_retrieval_plan(retrieval_data),
        vector_top_k=_as_int(retrieval_data.get("vector_top_k", 40)),
        rerank_top_n=_as_int(retrieval_data.get("rerank_top_n", 10)),
        layer_top_k=_as_int(retrieval_data.get("layer_top_k", 8)),
        layer_rerank_top_n=_as_int(retrieval_data.get("layer_rerank_top_n", 5)),
        max_reference_chunks=_as_int(retrieval_data.get("max_reference_chunks", 5)),
        expand_parent_payload=_as_bool(retrieval_data.get("expand_parent_payload", False)),
        parent_payload_max_chars=_as_int(retrieval_data.get("parent_payload_max_chars", 300)),
        parent_payload_include_neighbors=_as_bool(retrieval_data.get("parent_payload_include_neighbors", True)),
        parent_payload_neighbor_window=_as_int(retrieval_data.get("parent_payload_neighbor_window", 1)),
        parent_payload_include_raw_parent_text=_as_bool(retrieval_data.get("parent_payload_include_raw_parent_text", True)),
        layered_plan=copy.deepcopy(_as_list(retrieval_data.get("layered_plan"))),
    )
    grounding_data = _section(data, "grounding", GroundingConfig())
    grounding = GroundingConfig(
        evidence_strength_enabled=_as_bool(grounding_data.get("evidence_strength_enabled", True)),
        field_binding_enabled=_as_bool(grounding_data.get("field_binding_enabled", True)),
        min_strength_for_answered=str(grounding_data.get("min_strength_for_answered", "E3")),
        min_strength_for_writeback=str(grounding_data.get("min_strength_for_writeback", "E3")),
        require_target_source_for_answered=_as_bool(grounding_data.get("require_target_source_for_answered", True)),
        global_intro_answer_allowed=_as_bool(grounding_data.get("global_intro_answer_allowed", False)),
        downgrade_unsupported_answer_to_partial=_as_bool(grounding_data.get("downgrade_unsupported_answer_to_partial", False)),
        write_grounding_trace=_as_bool(grounding_data.get("write_grounding_trace", True)),
    )
    evaluation_data = _section(data, "evaluation", EvaluationConfig())
    evaluation = EvaluationConfig(
        default_rows=[_as_int(item) for item in _as_list(evaluation_data.get("default_rows"))],
        timeout_seconds=_as_int(evaluation_data.get("timeout_seconds", 120)),
        resume=_as_bool(evaluation_data.get("resume", False)),
        judge_model=str(evaluation_data.get("judge_model", "deepseek-v4-flash")),
        metrics_output=_resolve_path(evaluation_data.get("metrics_output", "artifacts/15_vector_store/base_cloud_closed_book_eval/summary.json"), root),
    )
    excel_data = _section(data, "excel", ExcelConfig())
    excel = ExcelConfig(
        template_path=_resolve_path(excel_data.get("template_path", "data/工勘单/基地云机房信息调研表.xlsx"), root),
        output_path=_resolve_path(excel_data.get("output_path", "artifacts/excel_outputs/base_cloud_filled.xlsx"), root),
        write_mode=str(excel_data.get("write_mode", "copy")),
        write_comments=_as_bool(excel_data.get("write_comments", True)),
        preserve_styles=_as_bool(excel_data.get("preserve_styles", True)),
    )
    agent_data = _section(data, "agent", AgentConfig())
    agent = AgentConfig(
        max_repair_attempts=_as_int(agent_data.get("max_repair_attempts", 2)),
        confidence_threshold=_as_float(agent_data.get("confidence_threshold", 0.72)),
        require_evidence_for_answer=_as_bool(agent_data.get("require_evidence_for_answer", True)),
        human_review_threshold=_as_float(agent_data.get("human_review_threshold", 0.55)),
        retrieval_backend=str(agent_data.get("retrieval_backend", "mini")),
        generation_backend=str(agent_data.get("generation_backend", "deterministic")),
        enable_rerank=_as_bool(agent_data.get("enable_rerank", False)),
        skip_generation_when_no_evidence=_as_bool(agent_data.get("skip_generation_when_no_evidence", True)),
        human_review_on_conflict=_as_bool(agent_data.get("human_review_on_conflict", True)),
        llm_json_repair=_as_bool(agent_data.get("llm_json_repair", True)),
    )
    agentscope_data = _section(data, "agentscope", AgentScopeConfig())
    agentscope_mode = _normalize_agentscope_mode(agentscope_data.get("mode", "off"))
    agentscope = AgentScopeConfig(
        enabled=_as_bool(agentscope_data.get("enabled", False)),
        mode=agentscope_mode,
    )
    if agentscope.mode not in {"off", "equivalent_mas", "trace_only"}:
        raise ValueError("agentscope.mode must be off, equivalent_mas, or trace_only")
    return AppConfig(
        paths=paths,
        services=services,
        qdrant=qdrant,
        retrieval=retrieval,
        grounding=grounding,
        evaluation=evaluation,
        excel=excel,
        agent=agent,
        agentscope=agentscope,
    )


def _normalize_agentscope_mode(value: Any) -> str:
    if value is False:
        return "off"
    return str(value or "off")


def load_yaml_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        parsed = parse_simple_yaml(text)
    else:
        loaded = yaml.safe_load(text) or {}
        parsed = loaded if isinstance(loaded, dict) else {}
    return parsed


def parse_simple_yaml(text: str) -> dict[str, Any]:
    lines: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        stripped_line = _strip_comment(raw_line).rstrip()
        if not stripped_line.strip():
            continue
        indent = len(stripped_line) - len(stripped_line.lstrip(" "))
        lines.append((indent, stripped_line.strip()))
    if not lines:
        return {}
    value, index = _parse_yaml_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError("could not parse complete YAML document")
    if not isinstance(value, dict):
        raise ValueError("top-level YAML value must be a mapping")
    return value


def env_to_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for name, value in env.items():
        if name.startswith(CONFIG_ENV_PREFIX):
            parts = [part.lower() for part in name[len(CONFIG_ENV_PREFIX) :].split("__") if part]
            if len(parts) >= 2:
                _set_nested(overrides, parts, _parse_scalar(value))
    for name, path in _env_aliases().items():
        if name in env and env[name] != "":
            _set_nested(overrides, list(path), _parse_scalar(env[name]))
    return overrides


def _env_aliases() -> dict[str, tuple[str, str]]:
    return {
        "NESTED_DOC_RAG_PROJECT_ROOT": ("paths", "project_root"),
        "NESTED_DOC_RAG_DATA_DIR": ("paths", "data_dir"),
        "NESTED_DOC_RAG_ARTIFACTS_DIR": ("paths", "artifacts_dir"),
        "NESTED_DOC_RAG_QDRANT_PATH": ("paths", "qdrant_path"),
        "EMBEDDING_ENDPOINT": ("services", "embedding_endpoint"),
        "EMBEDDING_MODEL": ("services", "embedding_model"),
        "RERANK_ENDPOINT": ("services", "rerank_endpoint"),
        "RERANK_MODEL": ("services", "rerank_model"),
        "CHAT_ENDPOINT": ("services", "chat_endpoint"),
        "CHAT_MODEL": ("services", "chat_model"),
        "CHAT_API_KEY_ENV": ("services", "chat_api_key_env"),
        "NDR_CHAT_ENDPOINT": ("services", "chat_endpoint"),
        "NDR_CHAT_MODEL": ("services", "chat_model"),
        "NDR_CHAT_API_KEY_ENV": ("services", "chat_api_key_env"),
        "NDR_EMBEDDING_ENDPOINT": ("services", "embedding_endpoint"),
        "NDR_EMBEDDING_MODEL": ("services", "embedding_model"),
        "NDR_RERANK_ENDPOINT": ("services", "rerank_endpoint"),
        "NDR_RERANK_MODEL": ("services", "rerank_model"),
        "DEEPSEEK_BASE_URL": ("services", "chat_endpoint"),
        "DEEPSEEK_MODEL": ("services", "chat_model"),
        "QDRANT_COLLECTION": ("qdrant", "collection_name"),
        "QDRANT_URL": ("qdrant", "url"),
        "QDRANT_API_KEY_ENV": ("qdrant", "api_key_env"),
        "QDRANT_PATH": ("paths", "qdrant_path"),
        "NDR_QDRANT_PATH": ("paths", "qdrant_path"),
        "NDR_QDRANT_COLLECTION": ("qdrant", "collection_name"),
        "NDR_QDRANT_URL": ("qdrant", "url"),
        "NDR_QDRANT_API_KEY_ENV": ("qdrant", "api_key_env"),
        "TARGET_NAMESPACE": ("retrieval", "target_namespace"),
        "RETRIEVAL_MODE": ("retrieval", "plan"),
        "NDR_RETRIEVAL_MODE": ("retrieval", "plan"),
        "NDR_RETRIEVAL_PLAN": ("retrieval", "plan"),
        "NDR_AGENT_RETRIEVAL_BACKEND": ("agent", "retrieval_backend"),
        "NDR_AGENT_GENERATION_BACKEND": ("agent", "generation_backend"),
        "NDR_AGENT_ENABLE_RERANK": ("agent", "enable_rerank"),
    }


def _parse_yaml_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    if lines[index][0] < indent:
        return {}, index
    if lines[index][1].startswith("- "):
        return _parse_yaml_list(lines, index, indent)
    return _parse_yaml_map(lines, index, indent)


def _parse_yaml_map(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    output: dict[str, Any] = {}
    while index < len(lines):
        line_indent, stripped = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ValueError(f"unexpected indentation: {stripped}")
        if stripped.startswith("- "):
            break
        key, value = _split_key_value(stripped)
        if value == "":
            if index + 1 < len(lines) and lines[index + 1][0] > line_indent:
                parsed, index = _parse_yaml_block(lines, index + 1, lines[index + 1][0])
                output[key] = parsed
            else:
                output[key] = {}
                index += 1
        else:
            output[key] = _parse_scalar(value)
            index += 1
    return output, index


def _parse_yaml_list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    output: list[Any] = []
    while index < len(lines):
        line_indent, stripped = lines[index]
        if line_indent < indent:
            break
        if line_indent != indent or not stripped.startswith("- "):
            break
        rest = stripped[2:].strip()
        if rest == "":
            if index + 1 < len(lines) and lines[index + 1][0] > line_indent:
                parsed, index = _parse_yaml_block(lines, index + 1, lines[index + 1][0])
            else:
                parsed, index = None, index + 1
            output.append(parsed)
            continue
        if _looks_like_mapping_item(rest):
            item: dict[str, Any] = {}
            key, value = _split_key_value(rest)
            item[key] = _parse_scalar(value) if value else {}
            index += 1
            if index < len(lines) and lines[index][0] > line_indent:
                nested, index = _parse_yaml_block(lines, index, lines[index][0])
                if isinstance(nested, dict):
                    item.update(nested)
                else:
                    item[key] = nested
            output.append(item)
        else:
            output.append(_parse_scalar(rest))
            index += 1
    return output, index


def _split_key_value(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise ValueError(f"expected key/value line: {text}")
    key, value = text.split(":", 1)
    return key.strip(), value.strip()


def _looks_like_mapping_item(text: str) -> bool:
    if ":" not in text:
        return False
    prefix = text.split(":", 1)[0].strip()
    return bool(re.match(r"^[A-Za-z0-9_.-]+$", prefix))


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    for index, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if index == 0 or line[index - 1].isspace():
                return line[:index]
    return line


def _parse_scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = os.path.expandvars(value.strip())
    if text == "":
        return ""
    if text[0] == text[-1:] and text[0] in {"'", '"'}:
        return text[1:-1]
    lowered = text.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", "~"}:
        return None
    if text.startswith("[") and text.endswith("]"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            inner = text[1:-1].strip()
            return [] if not inner else [_parse_scalar(part) for part in _split_inline_list(inner)]
    if text.startswith("{") and text.endswith("}"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if re.match(r"^-?\d+$", text):
        return int(text)
    if re.match(r"^-?\d+\.\d+$", text):
        return float(text)
    return text


def _split_inline_list(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    bracket_depth = 0
    for ch in text:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch in "[{" and not in_single and not in_double:
            bracket_depth += 1
        elif ch in "]}" and not in_single and not in_double:
            bracket_depth -= 1
        if ch == "," and not in_single and not in_double and bracket_depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


def _section(data: Mapping[str, Any], name: str, defaults: Any) -> dict[str, Any]:
    default_values = _serialize(defaults)
    values = data.get(name) or {}
    if isinstance(values, Mapping):
        default_values.update(values)
    return default_values


def _deep_merge(target: dict[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in source.items():
        if value is None:
            continue
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def _set_nested(target: dict[str, Any], parts: list[str], value: Any) -> None:
    current = target
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value if item is not None]
    return value


def _has_nested_key(data: Mapping[str, Any], parts: tuple[str, ...]) -> bool:
    current: Any = data
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return True


def _resolve_project_root(value: Any, base: Path) -> Path:
    if value in {None, ""}:
        return base.resolve()
    return _resolve_path(value, base)


def _resolve_path(value: Any, root: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            parsed = _parse_scalar(stripped)
            return parsed if isinstance(parsed, list) else [parsed]
        return [part.strip() for part in stripped.split(",") if part.strip()]
    return [value]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any) -> int:
    return int(value)


def _as_float(value: Any) -> float:
    return float(value)


def _serialize(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _serialize(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    return value
