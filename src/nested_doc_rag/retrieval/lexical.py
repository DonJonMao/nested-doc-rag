from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOKEN_PATTERN = re.compile(
    r"\d+\s*\*\s*\d+(?:\.\d+)?\s*[a-z]+"
    r"|[a-z]+/[a-z]+"
    r"|[a-z][\u4e00-\u9fff]"
    r"|\d+(?:\.\d+)?\s*(?:kva|kw|mw|w|g|gb|u|a|v|路|台|个|套)?"
    r"|[a-z]+(?:[-_/][a-z0-9]+)*"
    r"|[\u4e00-\u9fff]+",
    re.IGNORECASE,
)
NUMBER_UNIT_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)([a-z]+|路|台|个|套)?$")
QUERY_NUMBER_UNIT_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:kva|kw|mw|w|g|gb|u|a|v|路|台|个|套)", re.IGNORECASE)


def normalize_text_for_lexical(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("／", "/").replace("－", "-").replace("—", "-").replace("×", "*").replace("＊", "*")
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


def tokenize(value: Any) -> list[str]:
    text = normalize_text_for_lexical(value)
    tokens: list[str] = []
    for match in TOKEN_PATTERN.finditer(text):
        token = re.sub(r"\s+", "", match.group(0).lower())
        if not token:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            tokens.extend(chinese_ngrams(token))
            continue
        tokens.append(token)
        tokens.extend(auxiliary_tokens(token))
    return dedupe_keep_order(tokens)


def chinese_ngrams(text: str) -> list[str]:
    output: list[str] = []
    chars = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    if not chars:
        return output
    if len(chars) == 1:
        return chars
    for ngram_size in (2, 3):
        if len(chars) >= ngram_size:
            output.extend("".join(chars[index : index + ngram_size]) for index in range(0, len(chars) - ngram_size + 1))
    return output


def auxiliary_tokens(token: str) -> list[str]:
    output: list[str] = []
    compact = token.replace(" ", "")
    if "*" in compact:
        output.extend(part for part in compact.split("*") if part)
        output.extend(re.findall(r"\d+(?:\.\d+)?[a-z]+", compact))
    if "/" in compact:
        output.extend(part for part in compact.split("/") if part)
    match = NUMBER_UNIT_PATTERN.match(compact)
    if match:
        number, unit = match.groups()
        output.append(number)
        if unit:
            output.append(unit)
    if len(compact) == 2 and "\u4e00" <= compact[1] <= "\u9fff":
        output.append(compact[0])
        output.append(compact[1])
    return output


def exact_number_unit_terms(query: str) -> list[str]:
    text = normalize_text_for_lexical(query)
    return dedupe_keep_order([re.sub(r"\s+", "", match.group(0).lower()) for match in QUERY_NUMBER_UNIT_PATTERN.finditer(text)])


def dedupe_keep_order(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


@dataclass
class BM25Index:
    documents: list[dict[str, Any]]
    document_tokens: list[list[str]]
    document_frequencies: dict[str, int]
    avg_doc_length: float
    k1: float = 1.5
    b: float = 0.75
    version: int = 1
    _token_counts: list[Counter[str]] = field(default_factory=list, init=False, repr=False)

    @classmethod
    def from_records(cls, records: list[dict[str, Any]]) -> BM25Index:
        documents: list[dict[str, Any]] = []
        document_tokens: list[list[str]] = []
        document_frequencies: Counter[str] = Counter()
        for record in records:
            document = normalize_document(record)
            chunk_id = document.get("chunk_id")
            text = " ".join(
                str(item or "")
                for item in [
                    document.get("text_for_embedding"),
                    document.get("raw_text"),
                    document.get("anchor"),
                    document.get("file_name"),
                ]
            )
            tokens = tokenize(text)
            if not chunk_id or not tokens:
                continue
            documents.append(document)
            document_tokens.append(tokens)
            document_frequencies.update(set(tokens))
        avg_doc_length = sum(len(tokens) for tokens in document_tokens) / len(document_tokens) if document_tokens else 0.0
        index = cls(
            documents=documents,
            document_tokens=document_tokens,
            document_frequencies=dict(document_frequencies),
            avg_doc_length=avg_doc_length,
        )
        index._build_token_counts()
        return index

    @classmethod
    def from_jsonl(cls, path: Path) -> BM25Index:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return cls.from_records(records)

    @classmethod
    def load(cls, path: Path) -> BM25Index:
        payload = json.loads(path.read_text(encoding="utf-8"))
        index = cls(
            documents=[dict(item) for item in payload.get("documents") or []],
            document_tokens=[[str(token) for token in tokens] for tokens in payload.get("document_tokens") or []],
            document_frequencies={str(key): int(value) for key, value in (payload.get("document_frequencies") or {}).items()},
            avg_doc_length=float(payload.get("avg_doc_length") or 0.0),
            k1=float(payload.get("k1") or 1.5),
            b=float(payload.get("b") or 0.75),
            version=int(payload.get("version") or 1),
        )
        index._build_token_counts()
        return index

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.version,
            "k1": self.k1,
            "b": self.b,
            "avg_doc_length": self.avg_doc_length,
            "document_frequencies": self.document_frequencies,
            "documents": self.documents,
            "document_tokens": self.document_tokens,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def search(
        self,
        query: str,
        *,
        namespaces: list[str] | None = None,
        layers: list[str] | None = None,
        source_types: list[str] | None = None,
        source_documents: list[str] | None = None,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        if top_k <= 0 or not self.documents:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        exact_number_units = exact_number_unit_terms(query)
        namespace_set = set(namespaces or [])
        layer_set = set(layers or [])
        source_type_set = set(source_types or [])
        document_set = set(source_documents or [])
        scored: list[tuple[float, int]] = []
        for index, document in enumerate(self.documents):
            if not metadata_matches(document, namespace_set, layer_set, source_type_set, document_set):
                continue
            if exact_number_units and not any(token in self._token_counts[index] for token in exact_number_units):
                continue
            score = self.score_tokens(query_tokens, index)
            if score > 0:
                scored.append((score, index))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits: list[dict[str, Any]] = []
        for rank, (score, index) in enumerate(scored[:top_k], 1):
            document = dict(self.documents[index])
            document["bm25_rank"] = rank
            document["bm25_score"] = round(score, 6)
            document["score"] = document["bm25_score"]
            hits.append(document)
        return hits

    def score_tokens(self, query_tokens: list[str], document_index: int) -> float:
        if not self.avg_doc_length:
            return 0.0
        token_counts = self._token_counts[document_index]
        doc_length = len(self.document_tokens[document_index])
        total_documents = len(self.documents)
        score = 0.0
        for token in query_tokens:
            frequency = token_counts.get(token, 0)
            if not frequency:
                continue
            doc_frequency = self.document_frequencies.get(token, 0)
            idf = math.log(1 + (total_documents - doc_frequency + 0.5) / (doc_frequency + 0.5))
            denominator = frequency + self.k1 * (1 - self.b + self.b * doc_length / self.avg_doc_length)
            score += idf * (frequency * (self.k1 + 1)) / denominator
        return score

    def _build_token_counts(self) -> None:
        self._token_counts = [Counter(tokens) for tokens in self.document_tokens]


def normalize_document(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_document = (
        payload.get("source_document")
        or payload.get("document_id")
        or source.get("source_document")
        or source.get("document_id")
        or source.get("file_name")
        or payload.get("file_name")
    )
    return {
        **{str(key): value for key, value in payload.items() if key != "payload"},
        "chunk_id": str(payload.get("chunk_id") or record.get("chunk_id") or record.get("id") or ""),
        "text_for_embedding": str(payload.get("text_for_embedding") or payload.get("raw_text") or ""),
        "raw_text": str(payload.get("raw_text") or payload.get("text_for_embedding") or ""),
        "source_document": str(source_document or ""),
        "namespace": str(payload.get("namespace") or ""),
        "corpus_layer": str(payload.get("corpus_layer") or ""),
        "source_type": str(payload.get("source_type") or ""),
        "row_index": payload.get("row_index"),
        "sheet_name": payload.get("sheet_name"),
        "cell_range": payload.get("cell_range") or payload.get("target_cell") or payload.get("proof_cell_refs"),
        "anchor": payload.get("anchor") or payload.get("source_anchor"),
        "file_name": payload.get("file_name") or source.get("file_name"),
        "proof_attachment_ids": payload.get("proof_attachment_ids") or payload.get("evidence_attachment_ids") or [],
        "evidence_attachment_ids": payload.get("evidence_attachment_ids") or payload.get("proof_attachment_ids") or [],
        "source": source,
    }


def metadata_matches(
    document: dict[str, Any],
    namespaces: set[str],
    layers: set[str],
    source_types: set[str],
    source_documents: set[str],
) -> bool:
    if namespaces and str(document.get("namespace") or "") not in namespaces:
        return False
    if layers and str(document.get("corpus_layer") or "") not in layers:
        return False
    if source_types and str(document.get("source_type") or "") not in source_types:
        return False
    if source_documents:
        candidates = {
            str(document.get("source_document") or ""),
            str(document.get("document_id") or ""),
            str(document.get("file_name") or ""),
        }
        if not candidates.intersection(source_documents):
            return False
    return True
