"""Exact citation locations, independent of answer correctness and writeback gates.

Offsets are Unicode code points in ``source_text`` and use the half-open
``[start, end)`` convention. No normalization or fuzzy matching is performed.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from nested_doc_rag.schemas.eval import FieldPrediction

MATCH_STATUSES = ("exact", "ambiguous", "unmatched", "unavailable")


def locate_quote(hit: Mapping[str, Any] | None, quote: Any, *, chunk_id: str = "") -> dict[str, Any]:
    """Locate a model-provided quote in one authoritative retrieved chunk."""
    source = hit.get("source") if hit else None
    source = source if isinstance(source, Mapping) else {}
    version = (
        (hit or {}).get("index_version") or source.get("index_version")
        or (hit or {}).get("index_version_id") or source.get("index_version_id") or "unknown"
    )
    source_text = ""
    text_space = "unavailable"
    if hit:
        for key in ("raw_source_text", "raw_text"):
            value = hit.get(key)
            if isinstance(value, str) and value:
                source_text, text_space = value, key
                break
    candidate = quote if isinstance(quote, str) else ""
    actual_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest() if source_text else ""
    declared_hash = (hit or {}).get("source_text_hash") or source.get("source_text_hash")
    declared_space = (hit or {}).get("source_text_hash_space") or source.get("source_text_hash_space")
    check_declared_hash = bool(declared_hash) and (
        text_space == "raw_source_text" or (text_space == "raw_text" and declared_space == "raw_text")
    )
    normalized_declared_hash = str(declared_hash or "").removeprefix("sha256:").lower()
    result: dict[str, Any] = {
        "match_status": "unavailable",
        "reason": "",
        "quote": candidate,
        "start": None,
        "end": None,
        "source_text": source_text,
        "source_text_hash": actual_hash,
        "text_space": text_space,
        "index_version": str(version),
    }
    if not chunk_id:
        result["reason"] = "missing_chunk_id"
    elif hit is None:
        result["reason"] = "chunk_not_retrieved"
    elif not source_text:
        result["reason"] = "missing_source_text"
    elif check_declared_hash and normalized_declared_hash != actual_hash:
        result["reason"] = "source_hash_mismatch"
    elif not candidate.strip():
        result["reason"] = "missing_quote"
    else:
        start = source_text.find(candidate)
        if start < 0:
            result.update(match_status="unmatched", reason="quote_not_found")
        elif source_text.find(candidate, start + 1) >= 0:
            result.update(match_status="ambiguous", reason="quote_not_unique")
        else:
            result.update(match_status="exact", reason="quote_located", start=start, end=start + len(candidate))
    return result


def _value(record: Any, key: str, default: Any = None) -> Any:
    return record.get(key, default) if isinstance(record, Mapping) else getattr(record, key, default)


def _candidate_quote(reference: Mapping[str, Any]) -> str:
    for key in ("quote", "text_preview"):
        value = reference.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def source_reference(hit: Mapping[str, Any] | None, *, chunk_id: str) -> dict[str, Any]:
    """Copy source identity from retrieval, never from model-supplied metadata."""
    hit = hit or {}
    source = hit.get("source")
    source = source if isinstance(source, Mapping) else {}
    address = hit.get("address")
    address = address if isinstance(address, Mapping) else {}

    def first(*keys: str, default: Any = "") -> Any:
        for key in keys:
            for record in (hit, source, address):
                value = record.get(key)
                if value is not None and value != "":
                    return deepcopy(value)
        return deepcopy(default)

    def text(*keys: str) -> str:
        value = first(*keys)
        return str(value) if value is not None else ""

    raw_source = hit.get("raw_source_text") or hit.get("raw_text")
    excerpt = raw_source[:500] if isinstance(raw_source, str) else ""
    return {
        "chunk_id": chunk_id,
        "namespace": text("namespace"),
        "source_type": text("source_type"),
        "evidence_kind": text("evidence_kind"),
        "corpus_layer": text("corpus_layer"),
        "retrieval_layer": text("retrieval_layer"),
        "source_anchor": text("source_anchor", "anchor"),
        "anchor": text("anchor", "source_anchor"),
        "file_name": text("file_name", "source_document"),
        "relative_path": text("relative_path"),
        "document_id": text("document_id", "file_id"),
        "object_key": text("object_key"),
        "object_version_id": text("object_version_id"),
        "source_document_hash": text("source_document_hash"),
        "qdrant_point_id": text("qdrant_point_id", "point_id", "id"),
        "page": first("page", default=None),
        "sheet_name": text("sheet_name"),
        "cell": text("cell", "source_cell", "cell_range"),
        "cell_range": text("cell_range", "cell", "source_cell"),
        "row_index": first("row_index", default=None),
        "column_index": first("column_index", default=None),
        "table_index": first("table_index", default=None),
        "paragraph_index": first("paragraph_index", default=None),
        "bbox": first("bbox", default=[]),
        "caption": text("caption"),
        "image_object_key": text("image_object_key"),
        "proof_attachment_ids": [str(value) for value in first("proof_attachment_ids", default=[])],
        "proof_attachments": first("proof_attachments", default=[]),
        "text_preview": excerpt,
    }


def build_field_evidence(
    *,
    item: Mapping[str, Any],
    prediction: FieldPrediction,
    generated: Mapping[str, Any],
    top_hits: list[dict[str, Any]],
    overlay: Any | None,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    """Build a sidecar without changing the raw answer, overlay or policy."""
    from nested_doc_rag.excel.writeback import classify_writeback_status

    hits = {str(hit["chunk_id"]): hit for hit in top_hits if hit.get("chunk_id")}
    candidates: list[tuple[str, str]] = []
    for reference in generated.get("reference_source_documents") or []:
        if isinstance(reference, Mapping):
            candidates.append((str(reference.get("chunk_id") or ""), _candidate_quote(reference)))
    cited_ids = [*prediction.source_chunk_ids, *prediction.reference_chunk_ids]
    cited_ids.extend(str(value) for value in _value(overlay, "suggested_reference_chunk_ids", []) or [])
    for chunk_id in cited_ids:
        if not any(candidate_id == chunk_id for candidate_id, _ in candidates):
            candidates.append((chunk_id, ""))
    if not candidates:
        # Related source excerpts are readable, but are never invented quotes.
        candidates = [(str(hit["chunk_id"]), "") for hit in top_hits[:5] if hit.get("chunk_id")]
    if not candidates:
        candidates = [("", "")]

    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for chunk_id, quote in candidates:
        if (chunk_id, quote) in seen:
            continue
        seen.add((chunk_id, quote))
        hit = hits.get(chunk_id)
        ref = source_reference(hit, chunk_id=chunk_id)
        ref["provenance"] = locate_quote(hit, quote, chunk_id=chunk_id)
        if unavailable_reason:
            ref["provenance"].update(match_status="unavailable", reason=unavailable_reason, start=None, end=None)
        refs.append(ref)

    target_cell = prediction.target_cell or ""
    sheet_name = str(item.get("sheet_name") or "")
    if "!" in target_cell:
        sheet_name = target_cell.rsplit("!", 1)[0].strip("'").replace("''", "'")
    return {
        "field_id": prediction.field_id,
        "field_key": prediction.field_id,
        "question_text": str(item.get("question_text") or ""),
        "answer_value": deepcopy(prediction.answer_value),
        "answer_status": prediction.answer_status,
        "target_cell": prediction.target_cell,
        "sheet_name": sheet_name,
        "row_index": prediction.row_index,
        "writeback_status": classify_writeback_status(prediction, overlay),
        "writeback_action": "review_only",
        "writeback_allowed": bool(_value(overlay, "writeback_allowed", False)),
        "reasons": list(_value(overlay, "reasons", []) or []),
        "evidence_refs": refs,
        "addressed_evidence_refs": [ref.to_dict() for ref in prediction.evidence_refs],
        **({"acquisition": deepcopy(prediction.validation["acquisition"])} if isinstance(prediction.validation.get("acquisition"), Mapping) else {}),
    }


def finalize_evidence(
    fields: list[dict[str, Any]],
    *,
    audit_records: list[dict[str, Any]],
    writeback_status: str,
) -> dict[str, Any]:
    """Actual writeback audit takes precedence over pre-writeback classification."""
    audit_by_id = {str(record.get("field_id") or ""): record for record in audit_records}
    output = deepcopy(fields)
    counts: Counter[str] = Counter()
    for field in output:
        audit = audit_by_id.get(field["field_id"])
        if audit:
            field["writeback_status"] = audit.get("status") or field["writeback_status"]
            field["writeback_action"] = audit.get("writeback_action") or "review_only"
            if audit.get("sheet_name"):
                field["sheet_name"] = audit["sheet_name"]
            reason = audit.get("reason")
            for key in ("old_value", "new_value", "existing_value_policy"):
                if key in audit:
                    field[key] = deepcopy(audit[key])
        else:
            field["writeback_action"] = "review_only"
            reason = writeback_status if writeback_status != "completed" else "writeback_audit_unavailable"
        if reason and reason not in field["reasons"]:
            field["reasons"].append(reason)
        for ref in field["evidence_refs"]:
            counts[ref["provenance"]["match_status"]] += 1
    return {"summary": {status: counts[status] for status in MATCH_STATUSES}, "fields": output}
