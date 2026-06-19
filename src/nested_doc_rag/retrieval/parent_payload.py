from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from nested_doc_rag.io import display_text


@dataclass(frozen=True)
class ParentPayload:
    sheet_name: str | None
    table_title: str | None
    section_path: str | None
    row_header: str | None
    column_header: str | None
    unit: str | None
    scope: str | None
    status: str | None
    parent_text: str | None
    neighbor_text: str | None
    source_document: str | None
    source_type: str | None
    row_index: int | None
    cell_range: str | None
    confidence: str
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_parent_payload(
    hit: Mapping[str, Any],
    *,
    manifest_lookup=None,
    max_chars: int = 300,
    include_neighbors: bool = True,
    neighbor_window: int = 1,
    include_raw_parent_text: bool = True,
) -> ParentPayload:
    chunk_id = display_text(hit.get("chunk_id"))
    manifest_record = resolve_manifest_record(manifest_lookup, chunk_id)
    sources = [hit]
    if isinstance(hit.get("parent_payload"), Mapping):
        sources.append(hit["parent_payload"])
    if isinstance(hit.get("source"), Mapping):
        sources.append(hit["source"])
    if isinstance(hit.get("payload"), Mapping):
        sources.append(hit["payload"])
    if isinstance(manifest_record, Mapping):
        sources.append(manifest_record)
        if isinstance(manifest_record.get("payload"), Mapping):
            sources.append(manifest_record["payload"])
        if isinstance(manifest_record.get("source"), Mapping):
            sources.append(manifest_record["source"])

    raw_text = first_value(sources, "raw_text", "text_for_embedding", "content", "text")
    parsed = parse_parent_prefixes(raw_text)
    parsed_manifest = parse_parent_prefixes(first_value([manifest_record] if isinstance(manifest_record, Mapping) else [], "raw_text", "text_for_embedding"))
    parsed = {**parsed_manifest, **parsed}

    sheet_name = first_value(sources, "sheet_name", "sheet") or parsed.get("sheet_name")
    table_title = first_value(sources, "table_title", "table_name", "table_id") or parsed.get("table_title")
    section_path = stringify_path(first_value(sources, "section_path", "parent_section", "section_title")) or parsed.get("section_path")
    category = stringify_path(first_value(sources, "category", "category_path"))
    capability_desc = first_value(sources, "capability_desc", "field_name", "answer_key") or parsed.get("capability_desc")
    row_header = first_value(sources, "row_header", "row_title") or parsed.get("row_header") or category
    column_header = first_value(sources, "column_header", "column_title", "header") or parsed.get("column_header") or capability_desc
    unit = first_value(sources, "unit", "answer_unit") or parsed.get("unit") or infer_unit(raw_text)
    source_document = first_value(sources, "source_document", "file_name", "document_name")
    source_type = first_value(sources, "source_type")
    row_index = parse_int(first_value(sources, "row_index"))
    cell_range = first_value(sources, "cell_range", "cell", "target_cell")
    scope = first_value(sources, "scope") or parsed.get("scope") or infer_scope(hit, raw_text)
    status = first_value(sources, "status") or parsed.get("status") or infer_status(" ".join(display_text(value) for value in [table_title, section_path, row_header, column_header, raw_text]))

    parent_text = build_parent_text(
        sources=sources,
        parsed=parsed,
        sheet_name=sheet_name,
        table_title=table_title,
        section_path=section_path,
        row_header=row_header,
        column_header=column_header,
        unit=unit,
        scope=scope,
        status=status,
        raw_text=raw_text if include_raw_parent_text else None,
        max_chars=max_chars,
    )
    neighbor_text = build_neighbor_text(
        manifest_lookup=manifest_lookup,
        manifest_record=manifest_record,
        max_chars=max_chars,
        include_neighbors=include_neighbors,
        neighbor_window=neighbor_window,
    )
    structural_values = [sheet_name, table_title, section_path, row_header, column_header, unit, scope, status, parent_text, neighbor_text]
    structural_count = sum(1 for value in structural_values if display_text(value))
    reasons: list[str] = []
    if manifest_record:
        reasons.append("manifest_record_found")
    if parsed:
        reasons.append("parsed_raw_text_prefixes")
    if structural_count >= 4:
        confidence = "high"
    elif structural_count >= 2:
        confidence = "medium"
    elif structural_count >= 1:
        confidence = "low"
    else:
        confidence = "missing"
        reasons.append("no_parent_metadata")
    return ParentPayload(
        sheet_name=sheet_name or None,
        table_title=table_title or None,
        section_path=section_path or None,
        row_header=row_header or None,
        column_header=column_header or None,
        unit=unit or None,
        scope=scope or None,
        status=status or None,
        parent_text=parent_text or None,
        neighbor_text=neighbor_text or None,
        source_document=source_document or None,
        source_type=source_type or None,
        row_index=row_index,
        cell_range=cell_range or None,
        confidence=confidence,
        reasons=dedupe(reasons),
    )


def attach_parent_payloads(
    hits: list[dict],
    *,
    manifest_lookup=None,
    max_chars: int = 300,
    include_neighbors: bool = True,
    neighbor_window: int = 1,
    include_raw_parent_text: bool = True,
) -> list[dict]:
    output: list[dict] = []
    for hit in hits:
        copied = dict(hit)
        copied["parent_payload"] = build_parent_payload(
            copied,
            manifest_lookup=manifest_lookup,
            max_chars=max_chars,
            include_neighbors=include_neighbors,
            neighbor_window=neighbor_window,
            include_raw_parent_text=include_raw_parent_text,
        ).to_dict()
        output.append(copied)
    return output


def resolve_manifest_record(manifest_lookup, chunk_id: str) -> Any:
    if not manifest_lookup or not chunk_id:
        return None
    if isinstance(manifest_lookup, Mapping):
        return manifest_lookup.get(chunk_id)
    if callable(manifest_lookup):
        return manifest_lookup(chunk_id)
    return None


def first_value(sources: list[Any], *keys: str) -> str:
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key in keys:
            value = source.get(key)
            if display_text(value):
                return stringify_path(value)
    return ""


def stringify_path(value: Any) -> str:
    if isinstance(value, list):
        return " / ".join(display_text(item) for item in value if display_text(item))
    return display_text(value)


def parse_int(value: Any) -> int | None:
    text = display_text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_parent_prefixes(text: Any) -> dict[str, str]:
    raw = display_text(text)
    if not raw:
        return {}
    mapping = {
        "sheet_name": ["sheet", "工作表"],
        "table_title": ["表", "表格", "table"],
        "section_path": ["父段", "章节", "section"],
        "row_header": ["行标题", "行", "类别"],
        "column_header": ["列标题", "列", "字段", "能力描述"],
        "unit": ["单位"],
        "status": ["状态", "现状", "规划"],
        "scope": ["机房", "范围"],
    }
    parsed: dict[str, str] = {}
    for line in re.split(r"[\n；;]", raw):
        for key, labels in mapping.items():
            for label in labels:
                pattern = rf"(?:^|\s){re.escape(label)}\s*[:：]\s*([^|，,。；;\n]+)"
                match = re.search(pattern, line, flags=re.IGNORECASE)
                if match and display_text(match.group(1)):
                    value = display_text(match.group(1))
                    if key == "status" and label in {"现状", "规划"}:
                        value = "planned" if label == "规划" else "current"
                    parsed.setdefault(key, value)
    return parsed


def infer_unit(text: Any) -> str:
    match = re.search(r"(?:kVA|kW|MW|W|U位|U|路|台|个|套)", display_text(text), flags=re.IGNORECASE)
    return match.group(0) if match else ""


def infer_scope(hit: Mapping[str, Any], text: Any) -> str:
    raw = display_text(text)
    namespace = display_text(hit.get("namespace"))
    if "园区" in raw or namespace == "global":
        return "global"
    if "机房" in raw or namespace:
        return "target"
    return ""


def infer_status(text: Any) -> str:
    raw = display_text(text)
    if any(term in raw for term in ["具备", "条件", "可支持", "改造"]):
        return "conditional"
    if any(term in raw for term in ["规划", "计划", "未来", "拟建"]):
        return "planned"
    if any(term in raw for term in ["现网", "当前", "已建设", "已建", "现状"]):
        return "current"
    return ""


def build_parent_text(
    *,
    sources: list[Any],
    parsed: dict[str, str],
    sheet_name: str,
    table_title: str,
    section_path: str,
    row_header: str,
    column_header: str,
    unit: str,
    scope: str,
    status: str,
    raw_text: Any,
    max_chars: int,
) -> str:
    explicit = first_value(sources, "parent_text", "parent_context", "parent_payload_text")
    parts = [
        explicit,
        parsed.get("parent_text", ""),
        f"sheet={sheet_name}" if sheet_name else "",
        f"table={table_title}" if table_title else "",
        f"section={section_path}" if section_path else "",
        f"row={row_header}" if row_header else "",
        f"column={column_header}" if column_header else "",
        f"unit={unit}" if unit else "",
        f"scope={scope}" if scope else "",
        f"status={status}" if status else "",
    ]
    if raw_text and not explicit:
        prefix = re.split(r"(?<=。)|\n", display_text(raw_text), maxsplit=1)[0]
        parts.append(prefix)
    return clip_text(" / ".join(part for part in parts if display_text(part)), max_chars)


def build_neighbor_text(
    *,
    manifest_lookup,
    manifest_record: Any,
    max_chars: int,
    include_neighbors: bool,
    neighbor_window: int,
) -> str:
    if not include_neighbors or not isinstance(manifest_record, Mapping):
        return ""
    direct = manifest_record.get("neighbor_text") or manifest_record.get("neighbor_context")
    if display_text(direct):
        return clip_text(display_text(direct), max_chars)
    neighbors = manifest_record.get("neighbors")
    if isinstance(neighbors, list):
        texts = [display_text(item.get("raw_text") or item.get("text_for_embedding") or item) for item in neighbors[: max(0, neighbor_window * 2)]]
        return clip_text(" ".join(text for text in texts if text), max_chars)
    neighbor_ids = manifest_record.get("neighbor_ids")
    if isinstance(neighbor_ids, list) and manifest_lookup:
        records = [resolve_manifest_record(manifest_lookup, display_text(chunk_id)) for chunk_id in neighbor_ids[: max(0, neighbor_window * 2)]]
        texts = [
            display_text(record.get("raw_text") or record.get("text_for_embedding"))
            for record in records
            if isinstance(record, Mapping)
        ]
        return clip_text(" ".join(text for text in texts if text), max_chars)
    return ""


def clip_text(text: str, max_chars: int) -> str:
    clean = re.sub(r"\s+", " ", display_text(text)).strip()
    if max_chars <= 0:
        return ""
    if len(clean) <= max_chars:
        return clean
    return clean[: max(0, max_chars - 1)].rstrip() + "…"


def dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = display_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output
