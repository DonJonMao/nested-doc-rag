"""Resolve references from retrieved payloads and validate stored references.

No address or source text supplied by an answer model is authoritative here.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from openpyxl.utils import column_index_from_string

from nested_doc_rag.evidence_record import EVIDENCE_KINDS, EvidenceAddress, infer_evidence_kind
from nested_doc_rag.schemas.evidence import EvidenceRef

EV_REF_NOT_IN_RETRIEVAL = "EV_REF_NOT_IN_RETRIEVAL"
EV_REF_MISSING_ADDRESS = "EV_REF_MISSING_ADDRESS"
EV_CELL_RANGE_INVALID = "EV_CELL_RANGE_INVALID"
EV_FILE_NOT_IN_KB = "EV_FILE_NOT_IN_KB"
EV_ATTACHMENT_NOT_FOUND = "EV_ATTACHMENT_NOT_FOUND"
_RANGE = re.compile(r"^\$?([A-Z]{1,3})\$?([1-9]\d*)(?::\$?([A-Z]{1,3})\$?([1-9]\d*))?$")
_RETRIEVAL_ONLY_KEYS = {"vector_rank", "vector_score", "rerank_rank", "rerank_score", "retrieval_layer", "layer_order", "layer_rank"}
REVIEW_DISPLAY_REFERENCE_CONTRACT = "review-display-v1"
_TYPED_REFERENCE_FIELDS = {
    "knowledge_base_id", "index_version", "source_text", "source_text_hash", "source_text_hash_space",
    "source_text_policy", "attachment_ids", "source_row_indices", "source_chain", "local_anchor", "metadata",
    "start", "end",
}


def mark_review_display_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    """Label display metadata without relabelling a typed authority claim."""
    reference = deepcopy(dict(value))
    if not _TYPED_REFERENCE_FIELDS.intersection(reference):
        reference["reference_contract"] = REVIEW_DISPLAY_REFERENCE_CONTRACT
    return reference


def is_typed_evidence_reference(value: Mapping[str, Any]) -> bool:
    """Source-kind display metadata alone does not claim typed authorization.

    A display label cannot hide native text, hashes, scope or quote offsets.
    Confirmed references are validated independently of this discriminator.
    """
    if _TYPED_REFERENCE_FIELDS.intersection(value):
        return True
    if value.get("reference_contract") == REVIEW_DISPLAY_REFERENCE_CONTRACT:
        return False
    return "evidence_kind" in value


@dataclass(frozen=True)
class EvidenceResolution:
    refs: list[EvidenceRef] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resolvable(self) -> bool:
        return bool(self.refs) and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {"refs": [ref.to_dict() for ref in self.refs], "errors": deepcopy(self.errors), "resolvable": self.resolvable}


def _error(code: str, chunk_id: str, reason: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "chunk_id": chunk_id, "reason": reason, **details}


def _source(hit: Mapping[str, Any]) -> Mapping[str, Any]:
    source = hit.get("source") or {}
    return source if isinstance(source, Mapping) else {}


def _first(hit: Mapping[str, Any], *names: str) -> Any:
    for record in (hit, _source(hit)):
        for name in names:
            value = record.get(name)
            if value is not None and value != "":
                return deepcopy(value)
    return None


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _valid_cell_range(value: str) -> bool:
    match = _RANGE.fullmatch(value)
    if not match:
        return False
    col1, row1, col2, row2 = match.groups()
    left, right = column_index_from_string(col1), column_index_from_string(col2 or col1)
    top, bottom = int(row1), int(row2 or row1)
    return 1 <= left <= right <= 16384 and 1 <= top <= bottom <= 1048576


def _source_text(hit: Mapping[str, Any], chunk_id: str) -> tuple[str | None, str | None, str | None, list[dict[str, Any]]]:
    # Imported lazily because existing provenance helpers also consume eval and
    # Excel schemas. The locator remains the common Unicode/hash implementation.
    from nested_doc_rag.grounding.provenance import locate_quote

    raw = hit.get("raw_source_text")
    if not isinstance(raw, str):
        raw = _source(hit).get("raw_source_text")
    native = isinstance(raw, str)
    space = "raw_source_text" if native else "raw_text"
    declared_hash = _first(hit, "source_text_hash")
    declared_space = _first(hit, "source_text_hash_space")
    if not native:
        raw = _first(hit, "raw_text")
        if declared_space != "raw_text" or not declared_hash:
            return None, None, None, [_error(EV_REF_MISSING_ADDRESS, chunk_id, "native_source_text_unavailable")]
    if not isinstance(raw, str) or not raw.strip():
        return None, None, None, [_error(EV_REF_MISSING_ADDRESS, chunk_id, "source_text_empty")]
    if declared_space is not None and declared_space != space:
        return raw, space, _optional_text(declared_hash), [_error(EV_REF_NOT_IN_RETRIEVAL, chunk_id, "source_hash_space_mismatch")]
    authority = {**hit, space: raw}
    for record in (hit, _source(hit)):
        recorded_space = record.get("source_text_hash_space")
        if recorded_space and recorded_space != space:
            return raw, space, _optional_text(declared_hash), [_error(EV_REF_NOT_IN_RETRIEVAL, chunk_id, "source_hash_space_mismatch")]
        if record.get("source_text_hash"):
            declaration = {**authority, "source_text_hash": record["source_text_hash"], "source_text_hash_space": space}
            checked = locate_quote(declaration, raw, chunk_id=chunk_id)
            if checked["match_status"] != "exact":
                return raw, space, _optional_text(declared_hash), [_error(EV_REF_NOT_IN_RETRIEVAL, chunk_id, checked["reason"])]
    located = locate_quote(authority, raw, chunk_id=chunk_id)
    if located["match_status"] != "exact":
        return raw, space, _optional_text(declared_hash), [_error(EV_REF_NOT_IN_RETRIEVAL, chunk_id, located["reason"])]
    digest = _optional_text(declared_hash) or "sha256:" + located["source_text_hash"]
    return raw, space, digest, []


def _ref_from_hit(hit: Mapping[str, Any]) -> tuple[EvidenceRef, list[dict[str, Any]]]:
    chunk_id = str(hit.get("chunk_id") or hit.get("evidence_id") or "")
    address = EvidenceAddress.from_payload(hit).to_dict()
    anchor = address["source_anchor"]
    if isinstance(anchor, str) and "!" in anchor:
        sheet, location = anchor.rsplit("!", 1)
        if _valid_cell_range(location):
            address["sheet_name"] = address["sheet_name"] or sheet.strip("'").replace("''", "'")
            address["cell_range"] = address["cell_range"] or location
    indices = _first(hit, "source_row_indices") or []
    indices = [index for index in indices if isinstance(index, int) and not isinstance(index, bool) and index > 0]
    if address["table_index"] is None and isinstance(anchor, str):
        table = re.search(r"\btable (\d+)$", anchor)
        if table:
            address["table_index"] = int(table[1])
    if address["table_index"] is not None and address["row_index"] is None and len(indices) == 1:
        address["row_index"] = indices[0]
    source_text, space, digest, errors = _source_text(hit, chunk_id)
    if hit.get("retrieval_object") == "field_schema":
        errors.append(_error(EV_REF_MISSING_ADDRESS, chunk_id, "auxiliary_schema_is_not_source_evidence"))
    attachments = _first(hit, "proof_attachments") or []
    attachments = [deepcopy(dict(item)) for item in attachments if isinstance(item, Mapping)]
    attachment_ids = [str(item) for item in _first(hit, "proof_attachment_ids") or [] if item is not None]
    attachment_ids.extend(str(item["attachment_id"]) for item in attachments if item.get("attachment_id"))
    attachment_ids = list(dict.fromkeys(attachment_ids))
    try:
        kind = infer_evidence_kind(hit)
    except ValueError:
        kind = str(hit.get("evidence_kind") or "")
        errors.append(_error(EV_REF_MISSING_ADDRESS, chunk_id, "unsupported_evidence_kind"))
    metadata = deepcopy(dict(hit.get("metadata") or {}))
    metadata = {key: value for key, value in metadata.items() if key not in _RETRIEVAL_ONLY_KEYS}
    for key in ("source_type", "corpus_layer", "structural_path", "field_name", "field_value", "embedding_policy", "default_index", "parent_chunk_id", "parent_attachment_id", "embedded_object_id"):
        if key in hit:
            metadata[key] = deepcopy(hit[key])
    ref = EvidenceRef(
        chunk_id=chunk_id, knowledge_base_id=str(_first(hit, "knowledge_base_id") or ""),
        namespace=str(_first(hit, "namespace") or ""), evidence_kind=kind,
        **address, source_text=source_text, attachment_ids=attachment_ids,
        document_id=_optional_text(_first(hit, "document_id", "file_id")),
        index_version=_optional_text(_first(hit, "index_version", "index_version_id")),
        source_text_hash=digest, source_text_hash_space=space,
        source_text_policy="native" if space == "raw_source_text" else ("legacy_hash_verified" if space else None),
        source_document_hash=_optional_text(_first(hit, "source_document_hash", "document_hash")),
        qdrant_point_id=_optional_text(_first(hit, "qdrant_point_id", "point_id", "id")),
        table_id=_optional_text(_first(hit, "table_id")), source_row_indices=indices,
        embedded_file_name=_optional_text(_first(hit, "embedded_file_name")),
        parent_file_id=_optional_text(_first(hit, "parent_file_id")),
        parent_source_cell=_optional_text(_first(hit, "parent_source_cell", "source_cell")),
        source_chain=deepcopy(_first(hit, "source_chain") or []),
        local_anchor=deepcopy(_first(hit, "local_anchor") or {}),
        proof_attachments=attachments, metadata=metadata,
    )
    try:
        EvidenceRef.from_dict(ref.to_dict())
    except (TypeError, ValueError) as exc:
        errors.append(_error(EV_REF_MISSING_ADDRESS, chunk_id, "invalid_hit_metadata", detail=str(exc)))
    errors.extend(_address_errors(ref))
    return ref, errors


def _address_errors(ref: EvidenceRef) -> list[dict[str, Any]]:
    errors = []
    if not ref.file_name and not ref.relative_path:
        errors.append(_error(EV_FILE_NOT_IN_KB, ref.chunk_id, "missing_file_identity"))
    if ref.cell_range and not _valid_cell_range(ref.cell_range):
        errors.append(_error(EV_CELL_RANGE_INVALID, ref.chunk_id, "invalid_cell_range", cell_range=ref.cell_range))
    elif ref.cell_range and ref.row_index and ref.sheet_name and not ref.table_index:
        match = _RANGE.fullmatch(ref.cell_range)
        assert match is not None
        if not int(match[2]) <= ref.row_index <= int(match[4] or match[2]):
            errors.append(_error(EV_CELL_RANGE_INVALID, ref.chunk_id, "row_outside_cell_range", cell_range=ref.cell_range))
    excel = bool(ref.sheet_name and (ref.cell_range or ref.row_index))
    table = bool(ref.table_index and ref.row_index) or bool(ref.table_id and ref.source_row_indices)
    paragraph = bool(ref.paragraph_index)
    chunk = bool(ref.source_anchor) and ref.evidence_kind in {"document_chunk", "document_intro"}
    located = {
        "structured_field": excel,
        "table_row": table if ref.metadata.get("source_type") == "embedded_word_table" else excel or table,
        "paragraph": paragraph, "document_intro": paragraph or table or chunk,
        "document_chunk": excel or table or paragraph or chunk,
    }.get(ref.evidence_kind, False)
    if not located or ref.evidence_kind not in EVIDENCE_KINDS:
        errors.append(_error(EV_REF_MISSING_ADDRESS, ref.chunk_id, "missing_physical_address"))
    return errors


def _scope_errors(
    ref: EvidenceRef, *, known_knowledge_base_ids: Iterable[str] | None, knowledge_base_files: Mapping[str, Iterable[str]] | None,
) -> list[dict[str, Any]]:
    if known_knowledge_base_ids is not None and (not ref.knowledge_base_id or ref.knowledge_base_id not in set(known_knowledge_base_ids)):
        return [_error(EV_FILE_NOT_IN_KB, ref.chunk_id, "knowledge_base_out_of_scope", knowledge_base_id=ref.knowledge_base_id)]
    if knowledge_base_files is not None:
        files = set(knowledge_base_files.get(ref.knowledge_base_id) or [])
        if not ref.knowledge_base_id or not files.intersection(value for value in (ref.file_name, ref.relative_path, ref.document_id) if value):
            return [_error(EV_FILE_NOT_IN_KB, ref.chunk_id, "file_not_in_knowledge_base", knowledge_base_id=ref.knowledge_base_id)]
    return []


def validate_evidence_ref(
    ref: EvidenceRef | Mapping[str, Any], hit: Mapping[str, Any] | None, *,
    known_knowledge_base_ids: Iterable[str] | None = None, knowledge_base_files: Mapping[str, Iterable[str]] | None = None,
) -> list[dict[str, Any]]:
    try:
        ref = EvidenceRef.from_dict(ref.to_dict() if isinstance(ref, EvidenceRef) else ref)
    except (TypeError, ValueError) as exc:
        return [_error(EV_REF_MISSING_ADDRESS, str(ref.get("chunk_id") or "") if isinstance(ref, Mapping) else "", "invalid_reference_schema", detail=str(exc))]
    if hit is None or ref.chunk_id != str(hit.get("chunk_id") or hit.get("evidence_id") or ""):
        return [_error(EV_REF_NOT_IN_RETRIEVAL, ref.chunk_id, "chunk_not_retrieved")]
    try:
        expected, errors = _ref_from_hit(hit)
    except (AttributeError, TypeError, ValueError) as exc:
        return [_error(EV_REF_MISSING_ADDRESS, ref.chunk_id, "invalid_retrieved_payload", detail=str(exc))]
    errors.extend(_address_errors(ref))
    errors.extend(_scope_errors(ref, known_knowledge_base_ids=known_knowledge_base_ids, knowledge_base_files=knowledge_base_files))
    expected_dict, actual_dict = expected.to_dict(), ref.to_dict()
    subset_fields = {"attachment_ids", "proof_attachments", "quote", "start", "end"}
    for name, value in expected_dict.items():
        if name not in subset_fields and actual_dict[name] != value:
            code = EV_FILE_NOT_IN_KB if name in {"knowledge_base_id", "namespace", "file_name", "relative_path", "document_id", "source_document_hash"} else EV_REF_NOT_IN_RETRIEVAL
            errors.append(_error(code, ref.chunk_id, "reference_does_not_match_hit", field=name))
    allowed_ids = set(expected.attachment_ids)
    for attachment_id in ref.attachment_ids:
        if attachment_id not in allowed_ids:
            errors.append(_error(EV_ATTACHMENT_NOT_FOUND, ref.chunk_id, "attachment_not_in_hit", attachment_id=attachment_id))
    allowed_attachments = {str(attachment.get("attachment_id") or ""): attachment for attachment in expected.proof_attachments}
    for attachment in ref.proof_attachments:
        attachment_id = str(attachment.get("attachment_id") or "")
        if not attachment_id or allowed_attachments.get(attachment_id) != attachment:
            errors.append(_error(EV_ATTACHMENT_NOT_FOUND, ref.chunk_id, "attachment_metadata_not_in_hit", attachment_id=attachment_id))
    if ref.quote is not None or ref.start is not None or ref.end is not None:
        from nested_doc_rag.grounding.provenance import locate_quote

        quote_hit = {**hit, expected.source_text_hash_space or "raw_source_text": expected.source_text}
        located = locate_quote(quote_hit, ref.quote, chunk_id=ref.chunk_id)
        if located["match_status"] != "exact" or ref.start != located["start"] or ref.end != located["end"]:
            errors.append(_error(EV_REF_NOT_IN_RETRIEVAL, ref.chunk_id, "quote_span_not_verified"))
    return errors


def resolve_evidence_refs(
    source_chunk_ids: Iterable[str], top_hits: Iterable[Mapping[str, Any]], *,
    known_knowledge_base_ids: Iterable[str] | None = None, knowledge_base_files: Mapping[str, Iterable[str]] | None = None,
) -> EvidenceResolution:
    if known_knowledge_base_ids is not None:
        known_knowledge_base_ids = set(known_knowledge_base_ids)
    if knowledge_base_files is not None:
        knowledge_base_files = {key: set(values) for key, values in knowledge_base_files.items()}
    by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for hit in top_hits:
        for key in dict.fromkeys(str(hit.get(name) or "") for name in ("chunk_id", "evidence_id")):
            if key:
                by_id[key].append(hit)
    refs, errors = [], []
    seen = set()
    for selected_id in dict.fromkeys(str(value) for value in source_chunk_ids):
        hits = by_id.get(selected_id) or []
        if not hits:
            errors.append(_error(EV_REF_NOT_IN_RETRIEVAL, selected_id, "chunk_not_retrieved"))
            continue
        try:
            candidates = [_ref_from_hit(hit) for hit in hits]
        except (AttributeError, TypeError, ValueError) as exc:
            errors.append(_error(EV_REF_MISSING_ADDRESS, selected_id, "invalid_retrieved_payload", detail=str(exc)))
            continue
        expected = candidates[0][0]
        problems = [problem for _, candidate_errors in candidates for problem in candidate_errors]
        if any(candidate.to_dict() != expected.to_dict() for candidate, _ in candidates[1:]):
            errors.append(_error(EV_REF_NOT_IN_RETRIEVAL, selected_id, "conflicting_retrieval_hits"))
            continue
        errors.extend(problems)
        scoped = _scope_errors(expected, known_knowledge_base_ids=known_knowledge_base_ids, knowledge_base_files=knowledge_base_files)
        errors.extend(scoped)
        if not problems and not scoped and expected.chunk_id not in seen:
            refs.append(expected)
            seen.add(expected.chunk_id)
    return EvidenceResolution(refs=refs, errors=errors)
