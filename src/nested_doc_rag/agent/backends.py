from __future__ import annotations

import json
import re
import time
from hashlib import sha1
from typing import Any, Protocol

from nested_doc_rag import model_gateway
from nested_doc_rag.embedding import CurlJsonClient, RerankClient
from nested_doc_rag.retrieval import QdrantRetriever, rerank_hits
from nested_doc_rag.schemas.eval import FieldGold, FieldPrediction

from .policies import make_prediction_from_evidence, reference_snippets, reference_source_documents, retrieve_from_mini_corpus
from .prompts import build_field_answer_messages
from .state import EvidenceBundle, QueryPlan

DEFAULT_QUERY_LAYERS = ["fact", "evidence", "intro_doc", "raw_text", "meta"]


class EvidenceRetriever(Protocol):
    def retrieve(self, query_plan: QueryPlan, field: FieldGold) -> list[dict[str, Any]]:
        ...


class AnswerGenerator(Protocol):
    def generate(
        self,
        field: FieldGold,
        evidence_bundle: EvidenceBundle,
        query_plan: QueryPlan,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> FieldPrediction:
        ...


class MiniCorpusRetriever:
    backend_name = "mini"
    retrieval_plan = "flat"

    def __init__(self, corpus: list[dict[str, Any]]):
        self.corpus = corpus
        self.last_metadata: dict[str, Any] = {}

    def retrieve(self, query_plan: QueryPlan, field: FieldGold) -> list[dict[str, Any]]:
        hits = retrieve_from_mini_corpus(query_plan, self.corpus, field)
        self.last_metadata = {
            "retrieval_backend": self.backend_name,
            "hit_count": len(hits),
            "qdrant_hit_count": 0,
            "rerank_enabled": False,
            "fallback_used": False,
            "namespaces_queried": sorted({query_plan.target_namespace, *query_plan.fallback_namespaces}),
        }
        return hits


class QdrantEvidenceRetriever:
    backend_name = "qdrant"
    retrieval_plan = "flat"

    def __init__(
        self,
        *,
        qdrant_retriever: QdrantRetriever,
        enable_rerank: bool = False,
        rerank_client: RerankClient | None = None,
        rerank_top_n: int = 8,
        vector_top_k: int = 20,
        query_layers: list[str] | None = None,
    ):
        self.qdrant_retriever = qdrant_retriever
        self.enable_rerank = enable_rerank
        self.rerank_client = rerank_client
        self.rerank_top_n = rerank_top_n
        self.vector_top_k = vector_top_k
        self.query_layers = query_layers or DEFAULT_QUERY_LAYERS
        self.last_metadata: dict[str, Any] = {}

    def retrieve(self, query_plan: QueryPlan, field: FieldGold) -> list[dict[str, Any]]:
        del field
        started = time.perf_counter()
        queries = [query_plan.primary_query, *query_plan.fallback_queries]
        hits: list[dict[str, Any]] = []
        namespaces_queried: list[str] = []
        fallback_used = False

        for query in list(dict.fromkeys(item for item in queries if item)):
            target_hits = self.search_once(query, namespaces=[query_plan.target_namespace], source_types=query_plan.preferred_source_types)
            namespaces_queried.append(query_plan.target_namespace)
            hits.extend(target_hits)
            if len(dedupe_hits(hits)) >= self.vector_top_k:
                break
            fallback_namespaces = [namespace for namespace in query_plan.fallback_namespaces if namespace != query_plan.target_namespace]
            if fallback_namespaces:
                fallback_used = True
                fallback_hits = self.search_once(query, namespaces=fallback_namespaces, source_types=query_plan.preferred_source_types)
                namespaces_queried.extend(fallback_namespaces)
                hits.extend(fallback_hits)
            if len(dedupe_hits(hits)) >= self.vector_top_k:
                break

        normalized = [normalize_hit(hit) for hit in dedupe_hits(hits)]
        if self.enable_rerank and self.rerank_client and normalized:
            normalized = rerank_hits(query_plan.primary_query, normalized, self.rerank_top_n, self.rerank_client)
        normalized = normalized[: self.rerank_top_n if self.enable_rerank else self.vector_top_k]
        self.last_metadata = {
            "retrieval_backend": self.backend_name,
            "hit_count": len(normalized),
            "qdrant_hit_count": len(hits),
            "rerank_enabled": self.enable_rerank,
            "rerank_top_n": self.rerank_top_n,
            "fallback_used": fallback_used,
            "namespaces_queried": sorted(set(namespaces_queried)),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "collection_name": getattr(self.qdrant_retriever, "collection_name", ""),
        }
        return normalized

    def search_once(self, query: str, *, namespaces: list[str], source_types: list[str] | None) -> list[dict[str, Any]]:
        hits = self.qdrant_retriever.search(
            query,
            namespaces=namespaces,
            layers=self.query_layers,
            source_types=source_types,
            top_k=self.vector_top_k,
        )
        if not hits and source_types:
            hits = self.qdrant_retriever.search(
                query,
                namespaces=namespaces,
                layers=self.query_layers,
                source_types=None,
                top_k=self.vector_top_k,
            )
        return hits


class LayeredQdrantEvidenceRetriever:
    backend_name = "qdrant"
    retrieval_plan = "layered"

    def __init__(
        self,
        *,
        qdrant_retriever: QdrantRetriever,
        layered_plan: list[dict[str, Any]],
        global_namespace: str = "global",
        enable_rerank: bool = False,
        rerank_client: RerankClient | None = None,
        vector_top_k: int = 20,
        rerank_top_n: int = 8,
        max_reference_chunks: int = 5,
    ):
        self.qdrant_retriever = qdrant_retriever
        self.layered_plan = layered_plan
        self.global_namespace = global_namespace
        self.enable_rerank = enable_rerank
        self.rerank_client = rerank_client
        self.vector_top_k = vector_top_k
        self.rerank_top_n = rerank_top_n
        self.max_reference_chunks = max_reference_chunks
        self.last_metadata: dict[str, Any] = {}

    def retrieve(self, query_plan: QueryPlan, field: FieldGold) -> list[dict[str, Any]]:
        del field
        started = time.perf_counter()
        queries = list(dict.fromkeys(item for item in [query_plan.primary_query, *query_plan.fallback_queries] if item))
        hits: list[dict[str, Any]] = []
        vector_hit_count = 0
        namespaces_queried: list[str] = []
        layer_counts: dict[str, int] = {}
        fallback_used = False

        for query_index, query in enumerate(queries):
            query_hits: list[dict[str, Any]] = []
            for layer_priority, spec in enumerate(self.layered_plan, 1):
                layer_name = str(spec.get("layer_name") or f"layer_{layer_priority}")
                namespaces = self.layer_namespaces(spec, query_plan)
                if any(namespace != query_plan.target_namespace for namespace in namespaces):
                    fallback_used = True
                namespaces_queried.extend(namespaces)
                raw_hits = self.qdrant_retriever.search(
                    query,
                    namespaces=namespaces,
                    layers=[str(item) for item in spec.get("corpus_layers") or DEFAULT_QUERY_LAYERS],
                    source_types=[str(item) for item in spec.get("source_types") or []] or None,
                    top_k=int(spec.get("vector_top_k") or self.vector_top_k),
                )
                vector_hit_count += len(raw_hits)
                normalized = [
                    annotate_layer_hit(
                        normalize_hit(hit),
                        layer_name=layer_name,
                        layer_priority=layer_priority,
                        layer_rank=rank,
                        query_used=query,
                    )
                    for rank, hit in enumerate(raw_hits, 1)
                ]
                if self.enable_rerank and self.rerank_client and normalized:
                    normalized = rerank_hits(
                        query,
                        normalized,
                        int(spec.get("rerank_top_n") or self.rerank_top_n),
                        self.rerank_client,
                    )
                    for rank, hit in enumerate(normalized, 1):
                        hit["layer_rank"] = rank
                        hit["layer_score"] = hit.get("rerank_score") or hit.get("layer_score") or hit.get("vector_score") or hit.get("score")
                layer_counts[layer_name] = layer_counts.get(layer_name, 0) + len(normalized)
                query_hits.extend(normalized)
            hits.extend(query_hits)
            if query_index == 0 and query_hits:
                break

        deduped = dedupe_hits(hits)
        for final_rank, hit in enumerate(deduped, 1):
            hit["final_rank"] = final_rank
        self.last_metadata = {
            "retrieval_backend": self.backend_name,
            "retrieval_plan": "layered",
            "hit_count": len(deduped),
            "qdrant_hit_count": vector_hit_count,
            "rerank_enabled": self.enable_rerank,
            "rerank_top_n": self.rerank_top_n,
            "fallback_used": fallback_used,
            "namespaces_queried": sorted(set(namespaces_queried)),
            "layer_counts": layer_counts,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "collection_name": getattr(self.qdrant_retriever, "collection_name", ""),
            "max_reference_chunks": self.max_reference_chunks,
        }
        return deduped

    def layer_namespaces(self, spec: dict[str, Any], query_plan: QueryPlan) -> list[str]:
        namespace_spec = spec.get("namespaces") or "target"
        if namespace_spec == "target":
            return [query_plan.target_namespace]
        if namespace_spec == "global":
            return [self.global_namespace]
        if isinstance(namespace_spec, list):
            return [query_plan.target_namespace if item == "target" else self.global_namespace if item == "global" else str(item) for item in namespace_spec]
        return [str(namespace_spec)]


class DeterministicAnswerGenerator:
    backend_name = "deterministic"
    chat_model = ""

    def generate(
        self,
        field: FieldGold,
        evidence_bundle: EvidenceBundle,
        query_plan: QueryPlan,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> FieldPrediction:
        del query_plan, trace_context
        return make_prediction_from_evidence(field, evidence_bundle, method_name="field_filling_agent")


class LLMAnswerGenerator:
    backend_name = "llm"

    def __init__(
        self,
        *,
        chat_endpoint: str,
        chat_model: str,
        api_key: str | None = None,
        timeout_seconds: int = 120,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        http_client: CurlJsonClient | None = None,
    ):
        self.chat_endpoint = chat_endpoint
        self.chat_model = chat_model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.http = http_client or CurlJsonClient(timeout_seconds=timeout_seconds)

    def generate(
        self,
        field: FieldGold,
        evidence_bundle: EvidenceBundle,
        query_plan: QueryPlan,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> FieldPrediction:
        del trace_context
        if evidence_bundle.decision != "use_direct_evidence":
            return make_prediction_from_evidence(field, evidence_bundle)

        started = time.perf_counter()
        selected_chunk_ids = chunk_ids(evidence_bundle.selected_chunks)
        reference_chunk_ids = chunk_ids(evidence_bundle.reference_chunks)
        try:
            endpoint, headers = model_gateway.request_options(
                model_gateway.KIND_CHAT,
                self.chat_endpoint,
                "step15_answer",
                field_id=field.field_id,
                direct_headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else None,
            )
            response = self.http.post_json(
                endpoint,
                {
                    "model": self.chat_model,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                    "messages": build_field_answer_messages(field, evidence_bundle, query_plan),
                },
                headers=headers,
            )
            parsed = parse_chat_completion_json(response)
        except Exception as exc:  # noqa: BLE001 - turn generator failures into reviewable field predictions
            return generation_error_prediction(field, str(exc), self.chat_model, selected_chunk_ids)

        valid_sources, invalid_sources = validate_llm_sources(parsed.get("source_chunk_ids") or [], selected_chunk_ids)
        valid_references, invalid_references = validate_llm_sources(parsed.get("reference_chunk_ids") or [], reference_chunk_ids)
        answer_status = str(parsed.get("answer_status") or "not_found")
        if answer_status not in {"answered", "partial_clue", "not_found", "conflict_unresolved"}:
            answer_status = "conflict_unresolved"
        validation: dict[str, Any] = {
            "generation_backend": "llm",
            "chat_model": self.chat_model,
            "llm_reason": parsed.get("reason") or "",
            "selected_chunk_ids": selected_chunk_ids,
            "reference_chunk_ids": reference_chunk_ids,
            "generation_latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        if invalid_sources:
            validation["invalid_source_reference"] = invalid_sources
        if invalid_references:
            validation["invalid_reference_chunk_ids"] = invalid_references
        if answer_status == "answered" and not valid_sources:
            answer_status = "partial_clue"
            validation["missing_evidence"] = True
            valid_references = list(dict.fromkeys([*valid_references, *reference_chunk_ids]))
        if answer_status == "answered":
            valid_references = reference_chunk_ids
        elif answer_status == "partial_clue" and not valid_references:
            valid_references = reference_chunk_ids
        return FieldPrediction(
            field_id=field.field_id,
            row_index=field.row_index,
            target_cell=field.target_cell,
            answer_value=parsed.get("answer_value") or "未找到",
            answer_status=answer_status,
            confidence=min(float(parsed.get("confidence") or 0.0), 0.95),
            source_chunk_ids=valid_sources if answer_status == "answered" else [],
            evidence_attachment_ids=valid_attachment_ids(parsed.get("evidence_attachment_ids") or [], evidence_bundle.selected_chunks),
            reference_chunk_ids=valid_references,
            reference_source_documents=reference_source_documents(evidence_bundle.reference_chunks),
            reference_snippets=reference_snippets(evidence_bundle.reference_chunks),
            validation=validation,
            method_name="field_filling_agent_llm",
        )


def normalize_hit(hit: dict[str, Any]) -> dict[str, Any]:
    payload = hit.get("payload") if isinstance(hit.get("payload"), dict) else hit
    point_id = hit.get("id") or hit.get("point_id")
    raw_text = payload.get("raw_text") or payload.get("text") or payload.get("content") or ""
    text_for_embedding = payload.get("text_for_embedding") or raw_text
    chunk_id = payload.get("chunk_id") or hit.get("chunk_id") or point_id or stable_chunk_id(payload, raw_text)
    return {
        **{key: value for key, value in hit.items() if key not in {"payload"}},
        "chunk_id": str(chunk_id),
        "namespace": payload.get("namespace") or hit.get("namespace") or "",
        "source_type": payload.get("source_type") or hit.get("source_type") or "",
        "corpus_layer": payload.get("corpus_layer") or hit.get("corpus_layer") or "",
        "text_for_embedding": text_for_embedding,
        "raw_text": raw_text,
        "score": hit.get("score", hit.get("vector_score")),
        "vector_score": hit.get("vector_score", hit.get("score")),
        "rerank_score": hit.get("rerank_score"),
        "source": payload.get("source") or hit.get("source") or {},
        "source_anchor": payload.get("source_anchor") or payload.get("anchor") or hit.get("source_anchor") or hit.get("anchor") or payload.get("source") or hit.get("source") or {},
        "anchor": payload.get("anchor") or hit.get("anchor"),
        "file_name": payload.get("file_name") or hit.get("file_name"),
        "relative_path": payload.get("relative_path") or hit.get("relative_path"),
        "evidence_attachment_ids": payload.get("evidence_attachment_ids") or payload.get("proof_attachment_ids") or hit.get("evidence_attachment_ids") or [],
        "proof_attachment_ids": payload.get("proof_attachment_ids") or hit.get("proof_attachment_ids") or [],
        "proof_attachments": payload.get("proof_attachments") or hit.get("proof_attachments") or [],
    }


def annotate_layer_hit(
    hit: dict[str, Any],
    *,
    layer_name: str,
    layer_priority: int,
    layer_rank: int,
    query_used: str,
) -> dict[str, Any]:
    layer_score = hit.get("rerank_score") or hit.get("vector_score") or hit.get("score") or 0.0
    return {
        **hit,
        "retrieval_layer": layer_name,
        "layer_priority": layer_priority,
        "layer_rank": layer_rank,
        "layer_score": layer_score,
        "query_used": query_used,
    }


def dedupe_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for hit in hits:
        normalized = normalize_hit(hit)
        key = str(normalized.get("chunk_id") or stable_chunk_id(normalized, normalized.get("raw_text")))
        if key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return output


def stable_chunk_id(payload: dict[str, Any], raw_text: Any) -> str:
    source = json.dumps(payload.get("source") or {}, ensure_ascii=False, sort_keys=True)
    digest = sha1(f"{source}|{raw_text}".encode()).hexdigest()[:16]
    return f"hit_{digest}"


def parse_chat_completion_json(response: dict[str, Any]) -> dict[str, Any]:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"chat response missing choices[0].message.content: {response}") from exc
    return parse_json_object(str(content))


def parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise RuntimeError(f"LLM returned invalid JSON: {content[:300]}") from exc
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise RuntimeError(f"LLM JSON output must be an object: {value}")
    return value


def validate_llm_sources(raw_sources: Any, selected_chunk_ids: list[str]) -> tuple[list[str], list[str]]:
    if raw_sources is None:
        raw_sources = []
    elif isinstance(raw_sources, str):
        raw_sources = [raw_sources]
    elif not isinstance(raw_sources, list):
        raw_sources = [raw_sources]
    selected = set(selected_chunk_ids)
    valid: list[str] = []
    invalid: list[str] = []
    for item in raw_sources:
        chunk_id = str(item)
        if chunk_id in selected:
            valid.append(chunk_id)
        else:
            invalid.append(chunk_id)
    return list(dict.fromkeys(valid)), list(dict.fromkeys(invalid))


def valid_attachment_ids(raw_ids: list[Any], selected_chunks: list[dict[str, Any]]) -> list[str]:
    allowed: set[str] = set()
    for chunk in selected_chunks:
        allowed.update(str(item) for item in chunk.get("evidence_attachment_ids") or chunk.get("proof_attachment_ids") or [])
    return [str(item) for item in raw_ids if str(item) in allowed]


def chunk_ids(chunks: list[dict[str, Any]]) -> list[str]:
    return [str(chunk.get("chunk_id")) for chunk in chunks if chunk.get("chunk_id")]


def generation_error_prediction(field: FieldGold, error: str, chat_model: str, selected_chunk_ids: list[str]) -> FieldPrediction:
    return FieldPrediction(
        field_id=field.field_id,
        row_index=field.row_index,
        target_cell=field.target_cell,
        answer_value="未找到",
        answer_status="conflict_unresolved",
        confidence=0.0,
        source_chunk_ids=[],
        evidence_attachment_ids=[],
        validation={
            "generation_backend": "llm",
            "chat_model": chat_model,
            "generation_error": error,
            "needs_human_review": True,
            "selected_chunk_ids": selected_chunk_ids,
        },
        method_name="field_filling_agent_llm",
    )
