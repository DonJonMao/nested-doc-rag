from __future__ import annotations

from pathlib import Path
from typing import Any

from nested_doc_rag.io import write_json, write_jsonl

from .agentscope_bridge import AgentScopeRuntime, build_agentscope_runtime
from .roles import AnswerArbitrationRole, EvidenceRetrievalRole, OverlayControlRole, QueryPlannerRole, QueryReplannerRole, SkepticRole
from .schemas import ActionType, AgenticMASState, EvidenceAction, EvidenceRetrievalOutput, EvidenceStateKind
from .trace import MASTraceRecorder


class Step15MASController:
    def __init__(self, runner: Any, *, mode: str, agentscope_enabled: bool = False) -> None:
        self.runner = runner
        self.mode = mode
        self.trace = MASTraceRecorder()
        self.runtime: AgentScopeRuntime = build_agentscope_runtime(enabled=agentscope_enabled)
        self.query_planner = QueryPlannerRole(runner)
        self.evidence_retrieval = EvidenceRetrievalRole(runner)
        self.answer_arbitration = AnswerArbitrationRole(runner)
        self.overlay_control = OverlayControlRole(runner)
        self.query_replanner = QueryReplannerRole(runner)
        self.skeptic = SkepticRole(runner)
        self.agentic_trace = MASTraceRecorder()
        self.agentic_round_states: list[dict[str, Any]] = []
        self.agentic_summary_records: list[dict[str, Any]] = []

    def run_query_planner(self, item: dict[str, Any]) -> Any:
        return self.runtime.run_role(
            self.query_planner.name,
            _role_payload(item, stage="query_planner"),
            lambda: self.query_planner.run(item),
        )

    def run_evidence_retrieval(self, item: dict[str, Any], query_text: str) -> Any:
        return self.runtime.run_role(
            self.evidence_retrieval.name,
            {**_role_payload(item, stage="evidence_retrieval"), "query_length": len(query_text)},
            lambda: self.evidence_retrieval.run(query_text),
        )

    def run_query_replanner(self, state: AgenticMASState, *, workflow: str) -> list[EvidenceAction]:
        return self.runtime.run_role(
            self.query_replanner.name,
            {
                **_role_payload(state.item, stage="query_replanner"),
                "workflow": workflow,
                "round_index": state.round_index,
                "evidence_state": state.diagnosis.state_kind.value if state.diagnosis else None,
            },
            lambda: self.query_replanner.run(state, workflow=workflow),
        )

    def run_skeptic(self, state: AgenticMASState, *, workflow: str) -> list[EvidenceAction]:
        return self.runtime.run_role(
            self.skeptic.name,
            {
                **_role_payload(state.item, stage="skeptic"),
                "workflow": workflow,
                "round_index": state.round_index,
                "evidence_state": state.diagnosis.state_kind.value if state.diagnosis else None,
            },
            lambda: self.skeptic.run(state, workflow=workflow),
        )

    def run_evidence_retrieval_action(self, item: dict[str, Any], action: EvidenceAction) -> EvidenceRetrievalOutput:
        return self.runtime.run_role(
            self.evidence_retrieval.name,
            {
                **_role_payload(item, stage="evidence_retrieval_action"),
                "action_type": action.action_type.value,
                "query_length": len(action.query_text),
                "target_slot": action.target_slot,
                "target_layer": action.target_layer,
            },
            lambda: self.evidence_retrieval.run_action(action),
        )

    def run_answer_arbitration(
        self,
        item: dict[str, Any],
        query_text: str,
        top_hits: list[dict[str, Any]],
        *,
        slot_schema: dict[str, Any] | None = None,
    ) -> Any:
        return self.runtime.run_role(
            self.answer_arbitration.name,
            {
                **_role_payload(item, stage="answer_arbitration"),
                "query_length": len(query_text),
                "top_hit_count": len(top_hits),
                "slot_count": len((slot_schema or {}).get("slots") or []),
            },
            lambda: self.answer_arbitration.run(item, query_text, top_hits, slot_schema=slot_schema),
        )

    def run_overlay_control(self, item: dict[str, Any], generated: dict[str, Any], prediction: Any, top_hits: list[dict[str, Any]]) -> Any:
        return self.runtime.run_role(
            self.overlay_control.name,
            {
                **_role_payload(item, stage="overlay_control"),
                "answer_status": generated.get("answer_status"),
                "source_chunk_id_count": len(generated.get("source_chunk_ids") or []),
                "top_hit_count": len(top_hits),
            },
            lambda: self.overlay_control.run(item, generated, prediction, top_hits),
        )

    def process_item(self, item: dict[str, Any]) -> Any:
        from nested_doc_rag.agent.step15_runner import Step15FieldResult, field_id_for_item

        if self.mode == "agentic_mas":
            return self.process_item_agentic(item, config=getattr(self.runner, "agentic_mas_config", None))

        field_id = field_id_for_item(item)
        self.trace.record(field_id, "controller", "field_started", {"mode": self.mode, "agentscope_available": self.runtime.available})

        query_plan = self.run_query_planner(item)
        self.trace.record(
            field_id,
            self.query_planner.name,
            "query_planned",
            {"base_query": query_plan.base_query, "query_text": query_plan.query_text},
        )

        retrieval = self.run_evidence_retrieval(item, query_plan.query_text)
        self.trace.record(
            field_id,
            self.evidence_retrieval.name,
            "evidence_retrieved",
            {
                "top_hit_count": len(retrieval.top_hits),
                "vector_hit_count": len(retrieval.vector_hits),
                "retrieval_latency_ms": retrieval.retrieval_latency_ms,
            },
        )

        arbitration = self.run_answer_arbitration(item, query_plan.query_text, retrieval.top_hits)
        self.trace.record(
            field_id,
            self.answer_arbitration.name,
            "answer_arbitrated",
            {
                "answer_status": arbitration.generated.get("answer_status"),
                "generation_latency_ms": arbitration.generation_latency_ms,
            },
        )

        overlay = self.run_overlay_control(item, arbitration.generated, arbitration.prediction, retrieval.top_hits)
        self.trace.record(
            field_id,
            self.overlay_control.name,
            "overlay_controlled",
            {
                "critic_flags": overlay.critic_flags,
                "review_required": overlay.overlay.review_required,
                "writeback_allowed": overlay.overlay.writeback_allowed,
            },
        )

        return Step15FieldResult(
            item=item,
            masked_query=query_plan.query_text,
            prediction=arbitration.prediction,
            generated=arbitration.generated,
            top_hits=retrieval.top_hits,
            vector_hits=retrieval.vector_hits,
            overlay=overlay.overlay,
            review_item=overlay.review_item,
            eval_result=None,
            retrieval_latency_ms=retrieval.retrieval_latency_ms,
            generation_latency_ms=arbitration.generation_latency_ms,
            critic_flags=overlay.critic_flags,
        )

    def process_item_agentic(self, item: dict[str, Any], *, config: Any | None = None) -> Any:
        from nested_doc_rag.agent.step15_runner import (
            Step15FieldResult,
            attach_slot_validation,
            chunk_ids,
            count_layers,
            field_id_for_item,
            make_eval_result,
            make_step15_review_item,
            minimal_item_view,
        )
        from nested_doc_rag.io import display_text

        cfg = config or getattr(getattr(self.runner, "config", None), "agentic_mas", None)
        if cfg is None:
            raise RuntimeError("agentic_mas config is required for process_item_agentic")

        field_id = field_id_for_item(item)
        self.runner.trace.record(field_id, "field_started", {"field": minimal_item_view(item), "room_context": display_text(self.runner.room_context)})
        self.agentic_trace.record(
            field_id,
            "controller",
            "field_started",
            {"mode": self.mode, "agentscope_available": self.runtime.available, "max_rounds": cfg.max_rounds},
        )

        query_plan = self.run_query_planner(item)
        self.agentic_trace.record(
            field_id,
            self.query_planner.name,
            "query_planned",
            {"base_query": query_plan.base_query, "query_text": query_plan.query_text},
        )
        retrieval_query = query_plan.query_text
        self.runner.trace.record(
            field_id,
            "query_planned",
            {
                "masked_query_preview": display_text(retrieval_query, 240),
                "masked_query": retrieval_query,
                "retrieval_query_preview": display_text(retrieval_query, 240),
                "retrieval_query": retrieval_query,
                "room_context": display_text(self.runner.room_context),
            },
        )

        retrieval = self.run_evidence_retrieval(item, retrieval_query)
        retrieval_latency_ms = retrieval.retrieval_latency_ms
        top_hits = self._attach_parent_payloads(retrieval.top_hits)
        vector_hits = self._attach_parent_payloads(retrieval.vector_hits)
        self.agentic_trace.record(
            field_id,
            self.evidence_retrieval.name,
            "initial_evidence_retrieved",
            {
                "top_hit_count": len(top_hits),
                "vector_hit_count": len(vector_hits),
                "retrieval_latency_ms": retrieval.retrieval_latency_ms,
                "top_chunk_ids": chunk_ids(top_hits),
            },
        )
        self.runner.trace.record(
            field_id,
            "layered_retrieval_finished",
            {
                "retrieval_plan": self.runner.retrieval_plan,
                "total_hits": len(top_hits),
                "vector_hit_count": len(vector_hits),
                "layer_counts": count_layers(top_hits),
                "retrieval_latency_ms": retrieval.retrieval_latency_ms,
                "top_chunk_ids": chunk_ids(top_hits),
            },
        )

        slot_decomposition = self.runner.decompose_slots(item)
        self.runner.trace.record(field_id, "slot_decomposed", slot_decomposition.to_dict())
        slot_schema = slot_decomposition.to_prompt_dict()
        state = AgenticMASState(
            item=item,
            base_query=query_plan.base_query,
            current_query=retrieval_query,
            round_index=0,
            evidence=top_hits,
            vector_hits=vector_hits,
            generated=None,
            prediction=None,
            diagnosis=None,
            actions_taken=[],
        )

        generation_latency_ms = 0.0
        initial_status: str | None = None
        initial_answer: str | None = None
        triggered_workflows: list[str] = []
        retrieval_action_count = 0
        novel_chunk_count = 0

        for round_index in range(cfg.max_rounds):
            state.round_index = round_index
            arbitration = self.run_answer_arbitration(item, state.current_query, state.evidence, slot_schema=slot_schema)
            generation_latency_ms += arbitration.generation_latency_ms
            generated = arbitration.generated
            prediction = attach_slot_validation(arbitration.prediction, generated, slot_decomposition)
            state.generated = generated
            state.prediction = prediction
            state.diagnosis = arbitration.diagnosis
            if initial_status is None:
                initial_status = prediction.answer_status
                initial_answer = str(prediction.answer_value)

            diagnosis_payload = arbitration.diagnosis.to_dict() if arbitration.diagnosis is not None else None
            self.agentic_trace.record(
                field_id,
                self.answer_arbitration.name,
                "answer_arbitrated",
                {
                    "round_index": round_index,
                    "answer_status": generated.get("answer_status"),
                    "generation_latency_ms": arbitration.generation_latency_ms,
                    "diagnosis": diagnosis_payload,
                },
            )
            self.runner.trace.record(
                field_id,
                "answer_arbitrated",
                {
                    "chat_model": self.runner.chat_model,
                    "prompt_version": self.runner.prompt_version,
                    "round_index": round_index,
                    "answer_status": generated.get("answer_status"),
                    "source_chunk_ids": generated.get("source_chunk_ids") or [],
                    "reference_source_documents_count": len(generated.get("reference_source_documents") or []),
                    "generation_latency_ms": arbitration.generation_latency_ms,
                },
            )

            actions = self.select_actions(state, cfg)
            if round_index >= cfg.max_rounds - 1 and not _is_terminal_actions(actions):
                state.stopped_reason = "max_rounds"
                self.agentic_trace.record(
                    field_id,
                    "controller",
                    "agentic_stopped",
                    {"round_index": round_index, "stopped_reason": state.stopped_reason},
                )
                self.trace_round_state(state)
                break

            actions = actions[: cfg.max_actions_per_round]
            self.agentic_trace.record(
                field_id,
                "controller",
                "actions_selected",
                {
                    "round_index": round_index,
                    "actions": [action.to_dict() for action in actions],
                    "max_actions_per_round": cfg.max_actions_per_round,
                },
            )
            if not actions:
                state.stopped_reason = "no_actions"
                self.trace_round_state(state)
                break
            if _is_terminal_actions(actions):
                state.stopped_reason = _terminal_stopped_reason(actions, state)
                self.agentic_trace.record(
                    field_id,
                    "controller",
                    "agentic_stopped",
                    {"round_index": round_index, "stopped_reason": state.stopped_reason, "actions": [action.to_dict() for action in actions]},
                )
                self.trace_round_state(state)
                break

            round_new_chunk_ids: list[str] = []
            for action in actions:
                action_retrieval = self.run_evidence_retrieval_action(item, action)
                retrieval_latency_ms += action_retrieval.retrieval_latency_ms
                action_hits = self._attach_parent_payloads(action_retrieval.top_hits)
                action_vector_hits = self._attach_parent_payloads(action_retrieval.vector_hits)
                state.evidence, new_chunk_ids = self.merge_hits(state.evidence, action_hits)
                state.vector_hits, new_vector_chunk_ids = self.merge_hits(state.vector_hits, action_vector_hits)
                del new_vector_chunk_ids
                state.actions_taken.append(action)
                state.current_query = action.query_text
                retrieval_action_count += 1
                round_new_chunk_ids.extend(new_chunk_ids)
                novel_chunk_count += len(new_chunk_ids)
                workflow = workflow_for_action(action)
                if workflow:
                    triggered_workflows.append(workflow)
                self.agentic_trace.record(
                    field_id,
                    self.evidence_retrieval.name,
                    "agentic_action_retrieved",
                    {
                        "round_index": round_index,
                        "action": action.to_dict(),
                        "top_hit_count": len(action_hits),
                        "vector_hit_count": len(action_vector_hits),
                        "retrieval_latency_ms": action_retrieval.retrieval_latency_ms,
                        "new_chunk_ids": new_chunk_ids,
                        "novelty_count": len(new_chunk_ids),
                    },
                )

            if cfg.stop_on_no_novel_chunks and len(round_new_chunk_ids) < cfg.min_new_evidence:
                state.stopped_reason = "no_novel_chunks"
                self.agentic_trace.record(
                    field_id,
                    "controller",
                    "agentic_stopped",
                    {
                        "round_index": round_index,
                        "stopped_reason": state.stopped_reason,
                        "novelty_count": len(round_new_chunk_ids),
                        "min_new_evidence": cfg.min_new_evidence,
                    },
                )
                self.trace_round_state(state)
                break
            self.trace_round_state(state)

        if state.prediction is None or state.generated is None:
            raise RuntimeError("agentic_mas finished without an arbitration result")
        if state.stopped_reason is None:
            state.stopped_reason = "max_rounds"
            self.trace_round_state(state)

        prediction = state.prediction
        generated = state.generated
        top_hits = state.evidence
        vector_hits = state.vector_hits
        overlay_control = self.run_overlay_control(item, generated, prediction, top_hits)
        critic_flags = overlay_control.critic_flags
        overlay = overlay_control.overlay
        overlay, grounding_result = self.runner.apply_grounding_overlay(
            item=item,
            prediction=prediction,
            top_hits=top_hits,
            overlay=overlay,
            query_text=state.current_query,
        )
        overlay, binding_agent_result = self.runner.apply_field_binding_agent_overlay(
            item=item,
            prediction=prediction,
            top_hits=top_hits,
            overlay=overlay,
            grounding_result=grounding_result,
        )
        overlay, slot_result = self.runner.apply_slot_consistency_overlay(
            item=item,
            prediction=prediction,
            generated=generated,
            top_hits=top_hits,
            overlay=overlay,
            decomposition=slot_decomposition,
        )
        critic_flags = overlay.critic_flags
        review_item = make_step15_review_item(item, prediction, overlay, top_hits)
        self.agentic_trace.record(
            field_id,
            self.overlay_control.name,
            "overlay_controlled",
            {
                "critic_flags": critic_flags,
                "review_required": overlay.review_required,
                "writeback_allowed": overlay.writeback_allowed,
                "stopped_reason": state.stopped_reason,
            },
        )
        self.runner.trace.record(
            field_id,
            "agent_overlay_built",
            {
                "raw_status": prediction.answer_status,
                "suggested_status": overlay.suggested_status,
                "review_required": overlay.review_required,
                "writeback_allowed": overlay.writeback_allowed,
                "risk_level": overlay.risk_level,
                "reasons": overlay.reasons,
                "critic_flags": critic_flags,
                "evidence_strength": grounding_result.evidence_strength if grounding_result else None,
                "field_binding": grounding_result.field_binding if grounding_result else None,
                "field_binding_agent": binding_agent_result.to_dict(),
                "slot_consistency": slot_result.to_dict(),
                "suggested_reference_source_documents_count": len(overlay.suggested_reference_source_documents),
            },
        )
        self.runner.trace.record(
            field_id,
            "prediction_normalized",
            {"raw_prediction": prediction.to_dict(), "source_ids_valid": prediction.validation.get("source_ids_valid")},
        )
        self.runner.trace.record(field_id, "critic_checked", {"critic_flags": critic_flags})
        self.runner.trace.record(
            field_id,
            "review_routed",
            {
                "needs_review": review_item is not None,
                "suggested_action": (review_item or {}).get("suggested_action"),
                "critic_flags": critic_flags,
                "overlay": overlay.to_dict(),
            },
        )

        eval_result = None
        if self.runner.judge_enabled:
            heldout_answer = str(item.get("existing_value") or item.get("heldout_answer") or "")
            judge = self.runner.get_or_call_judge(item=item, generated=generated, heldout_answer=heldout_answer)
            eval_result = make_eval_result(item, generated, judge, top_hits, vector_hits, state.current_query, self.runner.room_context)
            self.runner.trace.record(
                field_id,
                "judge_completed",
                {"label": judge.get("label"), "score": judge.get("score"), "reason": judge.get("reason")},
            )

        final_status = prediction.answer_status
        final_answer = str(prediction.answer_value)
        self.agentic_summary_records.append(
            {
                "field_id": field_id,
                "row_index": item.get("row_index"),
                "rounds": (state.round_index + 1),
                "retrieval_actions": retrieval_action_count,
                "novel_chunks": novel_chunk_count,
                "initial_status": initial_status,
                "final_status": final_status,
                "status_changed": initial_status != final_status,
                "answer_changed": initial_answer != final_answer,
                "triggered_workflows": sorted(set(triggered_workflows)),
                "stopped_reason": state.stopped_reason,
            }
        )

        return Step15FieldResult(
            item=item,
            masked_query=state.current_query,
            prediction=prediction,
            generated=generated,
            top_hits=top_hits,
            vector_hits=vector_hits,
            overlay=overlay,
            review_item=review_item,
            eval_result=eval_result,
            retrieval_latency_ms=round(retrieval_latency_ms, 3),
            generation_latency_ms=round(generation_latency_ms, 3),
            critic_flags=critic_flags,
        )

    def record_live_base_preserved(self, *, item: dict[str, Any], base_result: Any) -> None:
        from nested_doc_rag.agent.step15_runner import field_id_for_item

        field_id = field_id_for_item(item)
        self.agentic_trace.record(
            field_id,
            "controller",
            "live_base_preserved",
            {
                "base_status": base_result.prediction.answer_status,
                "base_answer_value": base_result.prediction.answer_value,
                "base_writeback_allowed": base_result.overlay.writeback_allowed,
                "reason": "current_run_writeback37_writeback_allowed",
            },
        )
        self.agentic_summary_records.append(
            {
                "field_id": field_id,
                "row_index": item.get("row_index"),
                "rounds": 0,
                "retrieval_actions": 0,
                "novel_chunks": 0,
                "initial_status": base_result.prediction.answer_status,
                "final_status": base_result.prediction.answer_status,
                "status_changed": False,
                "answer_changed": False,
                "triggered_workflows": [],
                "stopped_reason": "base_writeback_allowed",
                "base_status": base_result.prediction.answer_status,
                "base_writeback_allowed": base_result.overlay.writeback_allowed,
                "fourmode_attempted": False,
            }
        )

    def process_item_agentic_from_base(self, item: dict[str, Any], *, base_result: Any, config: Any | None = None) -> Any:
        from nested_doc_rag.agent.step15_runner import (
            Step15FieldResult,
            attach_slot_validation,
            chunk_ids,
            field_id_for_item,
            make_eval_result,
            make_step15_review_item,
        )

        cfg = config or getattr(getattr(self.runner, "config", None), "agentic_mas", None)
        if cfg is None:
            raise RuntimeError("agentic_mas config is required for process_item_agentic_from_base")

        field_id = field_id_for_item(item)
        query_plan = self.run_query_planner(item)
        retrieval_query = base_result.masked_query or query_plan.query_text
        top_hits = self._attach_parent_payloads(list(base_result.top_hits))
        vector_hits = self._attach_parent_payloads(list(base_result.vector_hits))
        self.agentic_trace.record(
            field_id,
            "controller",
            "field_started",
            {
                "mode": self.mode,
                "from_current_run_writeback37_base": True,
                "agentscope_available": self.runtime.available,
                "max_rounds": cfg.max_rounds,
                "base_answer_status": base_result.prediction.answer_status,
                "base_writeback_allowed": base_result.overlay.writeback_allowed,
            },
        )
        self.agentic_trace.record(
            field_id,
            self.query_planner.name,
            "query_planned",
            {"base_query": query_plan.base_query, "query_text": retrieval_query, "from_base_result": True},
        )
        self.agentic_trace.record(
            field_id,
            self.evidence_retrieval.name,
            "initial_evidence_reused",
            {
                "top_hit_count": len(top_hits),
                "vector_hit_count": len(vector_hits),
                "top_chunk_ids": chunk_ids(top_hits),
            },
        )

        slot_decomposition = self.runner.decompose_slots(item)
        slot_schema = slot_decomposition.to_prompt_dict()
        state = AgenticMASState(
            item=item,
            base_query=query_plan.base_query,
            current_query=retrieval_query,
            round_index=0,
            evidence=top_hits,
            vector_hits=vector_hits,
            generated=None,
            prediction=None,
            diagnosis=None,
            actions_taken=[],
        )

        generation_latency_ms = 0.0
        retrieval_latency_ms = float(base_result.retrieval_latency_ms or 0.0)
        initial_status: str | None = None
        initial_answer: str | None = None
        triggered_workflows: list[str] = []
        retrieval_action_count = 0
        novel_chunk_count = 0

        for round_index in range(cfg.max_rounds):
            state.round_index = round_index
            arbitration = self.run_answer_arbitration(item, state.current_query, state.evidence, slot_schema=slot_schema)
            generation_latency_ms += arbitration.generation_latency_ms
            generated = arbitration.generated
            prediction = attach_slot_validation(arbitration.prediction, generated, slot_decomposition)
            state.generated = generated
            state.prediction = prediction
            state.diagnosis = arbitration.diagnosis
            if initial_status is None:
                initial_status = prediction.answer_status
                initial_answer = str(prediction.answer_value)

            diagnosis_payload = arbitration.diagnosis.to_dict() if arbitration.diagnosis is not None else None
            self.agentic_trace.record(
                field_id,
                self.answer_arbitration.name,
                "answer_arbitrated",
                {
                    "round_index": round_index,
                    "answer_status": generated.get("answer_status"),
                    "generation_latency_ms": arbitration.generation_latency_ms,
                    "diagnosis": diagnosis_payload,
                    "from_base_result": round_index == 0,
                },
            )
            self.runner.trace.record(
                field_id,
                "answer_arbitrated",
                {
                    "chat_model": self.runner.chat_model,
                    "prompt_version": getattr(self.runner, "agentic_prompt_version", self.runner.prompt_version),
                    "round_index": round_index,
                    "answer_status": generated.get("answer_status"),
                    "source_chunk_ids": generated.get("source_chunk_ids") or [],
                    "reference_source_documents_count": len(generated.get("reference_source_documents") or []),
                    "generation_latency_ms": arbitration.generation_latency_ms,
                    "from_base_result": round_index == 0,
                },
            )

            actions = self.select_actions(state, cfg)
            if round_index >= cfg.max_rounds - 1 and not _is_terminal_actions(actions):
                state.stopped_reason = "max_rounds"
                self.agentic_trace.record(
                    field_id,
                    "controller",
                    "agentic_stopped",
                    {"round_index": round_index, "stopped_reason": state.stopped_reason},
                )
                self.trace_round_state(state)
                break

            actions = actions[: cfg.max_actions_per_round]
            self.agentic_trace.record(
                field_id,
                "controller",
                "actions_selected",
                {
                    "round_index": round_index,
                    "actions": [action.to_dict() for action in actions],
                    "max_actions_per_round": cfg.max_actions_per_round,
                },
            )
            if not actions:
                state.stopped_reason = "no_actions"
                self.trace_round_state(state)
                break
            if _is_terminal_actions(actions):
                state.stopped_reason = _terminal_stopped_reason(actions, state)
                self.agentic_trace.record(
                    field_id,
                    "controller",
                    "agentic_stopped",
                    {"round_index": round_index, "stopped_reason": state.stopped_reason, "actions": [action.to_dict() for action in actions]},
                )
                self.trace_round_state(state)
                break

            round_new_chunk_ids: list[str] = []
            for action in actions:
                action_retrieval = self.run_evidence_retrieval_action(item, action)
                retrieval_latency_ms += action_retrieval.retrieval_latency_ms
                action_hits = self._attach_parent_payloads(action_retrieval.top_hits)
                action_vector_hits = self._attach_parent_payloads(action_retrieval.vector_hits)
                state.evidence, new_chunk_ids = self.merge_hits(state.evidence, action_hits)
                state.vector_hits, new_vector_chunk_ids = self.merge_hits(state.vector_hits, action_vector_hits)
                del new_vector_chunk_ids
                state.actions_taken.append(action)
                state.current_query = action.query_text
                retrieval_action_count += 1
                round_new_chunk_ids.extend(new_chunk_ids)
                novel_chunk_count += len(new_chunk_ids)
                workflow = workflow_for_action(action)
                if workflow:
                    triggered_workflows.append(workflow)
                self.agentic_trace.record(
                    field_id,
                    self.evidence_retrieval.name,
                    "agentic_action_retrieved",
                    {
                        "round_index": round_index,
                        "action": action.to_dict(),
                        "top_hit_count": len(action_hits),
                        "vector_hit_count": len(action_vector_hits),
                        "retrieval_latency_ms": action_retrieval.retrieval_latency_ms,
                        "new_chunk_ids": new_chunk_ids,
                        "novelty_count": len(new_chunk_ids),
                    },
                )

            if cfg.stop_on_no_novel_chunks and len(round_new_chunk_ids) < cfg.min_new_evidence:
                state.stopped_reason = "no_novel_chunks"
                self.agentic_trace.record(
                    field_id,
                    "controller",
                    "agentic_stopped",
                    {
                        "round_index": round_index,
                        "stopped_reason": state.stopped_reason,
                        "novelty_count": len(round_new_chunk_ids),
                        "min_new_evidence": cfg.min_new_evidence,
                    },
                )
                self.trace_round_state(state)
                break
            self.trace_round_state(state)

        if state.prediction is None or state.generated is None:
            raise RuntimeError("agentic_mas from base finished without an arbitration result")
        if state.stopped_reason is None:
            state.stopped_reason = "max_rounds"
            self.trace_round_state(state)

        prediction = state.prediction
        generated = state.generated
        top_hits = state.evidence
        vector_hits = state.vector_hits
        overlay_control = self.run_overlay_control(item, generated, prediction, top_hits)
        overlay = overlay_control.overlay
        overlay, grounding_result = self.runner.apply_grounding_overlay(
            item=item,
            prediction=prediction,
            top_hits=top_hits,
            overlay=overlay,
            query_text=state.current_query,
        )
        overlay, binding_agent_result = self.runner.apply_field_binding_agent_overlay(
            item=item,
            prediction=prediction,
            top_hits=top_hits,
            overlay=overlay,
            grounding_result=grounding_result,
        )
        overlay, slot_result = self.runner.apply_slot_consistency_overlay(
            item=item,
            prediction=prediction,
            generated=generated,
            top_hits=top_hits,
            overlay=overlay,
            decomposition=slot_decomposition,
        )
        critic_flags = overlay.critic_flags
        review_item = make_step15_review_item(item, prediction, overlay, top_hits)
        self.agentic_trace.record(
            field_id,
            self.overlay_control.name,
            "overlay_controlled",
            {
                "critic_flags": critic_flags,
                "review_required": overlay.review_required,
                "writeback_allowed": overlay.writeback_allowed,
                "stopped_reason": state.stopped_reason,
            },
        )
        self.runner.trace.record(
            field_id,
            "agent_overlay_built",
            {
                "raw_status": prediction.answer_status,
                "suggested_status": overlay.suggested_status,
                "review_required": overlay.review_required,
                "writeback_allowed": overlay.writeback_allowed,
                "risk_level": overlay.risk_level,
                "reasons": overlay.reasons,
                "critic_flags": critic_flags,
                "evidence_strength": grounding_result.evidence_strength if grounding_result else None,
                "field_binding": grounding_result.field_binding if grounding_result else None,
                "field_binding_agent": binding_agent_result.to_dict(),
                "slot_consistency": slot_result.to_dict(),
                "suggested_reference_source_documents_count": len(overlay.suggested_reference_source_documents),
            },
        )
        self.runner.trace.record(
            field_id,
            "prediction_normalized",
            {"raw_prediction": prediction.to_dict(), "source_ids_valid": prediction.validation.get("source_ids_valid")},
        )
        self.runner.trace.record(field_id, "critic_checked", {"critic_flags": critic_flags})
        self.runner.trace.record(
            field_id,
            "review_routed",
            {
                "needs_review": review_item is not None,
                "suggested_action": (review_item or {}).get("suggested_action"),
                "critic_flags": critic_flags,
                "overlay": overlay.to_dict(),
            },
        )

        eval_result = None
        if self.runner.judge_enabled:
            heldout_answer = str(item.get("existing_value") or item.get("heldout_answer") or "")
            judge = self.runner.get_or_call_judge(item=item, generated=generated, heldout_answer=heldout_answer)
            eval_result = make_eval_result(item, generated, judge, top_hits, vector_hits, state.current_query, self.runner.room_context)
            self.runner.trace.record(
                field_id,
                "judge_completed",
                {"label": judge.get("label"), "score": judge.get("score"), "reason": judge.get("reason")},
            )

        final_status = prediction.answer_status
        final_answer = str(prediction.answer_value)
        self.agentic_summary_records.append(
            {
                "field_id": field_id,
                "row_index": item.get("row_index"),
                "rounds": (state.round_index + 1),
                "retrieval_actions": retrieval_action_count,
                "novel_chunks": novel_chunk_count,
                "initial_status": initial_status,
                "final_status": final_status,
                "status_changed": initial_status != final_status,
                "answer_changed": initial_answer != final_answer,
                "triggered_workflows": sorted(set(triggered_workflows)),
                "stopped_reason": state.stopped_reason,
                "base_status": base_result.prediction.answer_status,
                "base_writeback_allowed": base_result.overlay.writeback_allowed,
                "fourmode_attempted": True,
            }
        )

        return Step15FieldResult(
            item=item,
            masked_query=state.current_query,
            prediction=prediction,
            generated=generated,
            top_hits=top_hits,
            vector_hits=vector_hits,
            overlay=overlay,
            review_item=review_item,
            eval_result=eval_result,
            retrieval_latency_ms=round(retrieval_latency_ms, 3),
            generation_latency_ms=round(float(base_result.generation_latency_ms or 0.0) + generation_latency_ms, 3),
            critic_flags=critic_flags,
        )

    def select_actions(self, state: AgenticMASState, cfg: Any) -> list[EvidenceAction]:
        diagnosis = state.diagnosis
        if diagnosis is None:
            return [_stop_action(state, "no diagnosis")]
        if diagnosis.state_kind == EvidenceStateKind.SUFFICIENT:
            return [_stop_action(state, "evidence sufficient")]
        if diagnosis.state_kind == EvidenceStateKind.MISSING_INFO:
            if cfg.workflows.missing_info.enabled:
                return self._filter_actions(self.run_query_replanner(state, workflow="missing_info"), cfg)
            return [_mark_unresolved_action(state, "missing_info workflow disabled")]
        if diagnosis.state_kind == EvidenceStateKind.WRONG_ANSWER_RISK:
            if cfg.workflows.wrong_answer_risk.enabled:
                return self._filter_actions(self.run_skeptic(state, workflow="wrong_answer_risk"), cfg)
            return [_mark_unresolved_action(state, "wrong_answer_risk workflow disabled")]
        if diagnosis.state_kind == EvidenceStateKind.NOT_FOUND_RECOVERY:
            if cfg.workflows.not_found_recovery.enabled:
                return self._filter_actions(self.run_query_replanner(state, workflow="not_found_recovery"), cfg)
            return [_mark_unresolved_action(state, "not_found_recovery workflow disabled")]
        if diagnosis.state_kind == EvidenceStateKind.UNCERTAINTY_CONFLICT:
            if cfg.workflows.uncertainty_conflict.enabled:
                actions = [
                    *self.run_query_replanner(state, workflow="uncertainty_conflict"),
                    *self.run_skeptic(state, workflow="uncertainty_conflict"),
                ]
                return self._filter_actions(actions, cfg)
            return [_mark_unresolved_action(state, "uncertainty_conflict workflow disabled")]
        return [_mark_unresolved_action(state, f"unhandled evidence state: {diagnosis.state_kind.value}")]

    def merge_hits(self, existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        seen = {str(hit.get("chunk_id")) for hit in existing if hit.get("chunk_id")}
        merged = list(existing)
        novel_chunk_ids: list[str] = []
        for hit in incoming:
            chunk_id = str(hit.get("chunk_id") or "")
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            merged.append(hit)
            novel_chunk_ids.append(chunk_id)
        return merged, novel_chunk_ids

    def trace_round_state(self, state: AgenticMASState) -> None:
        record = state.to_dict()
        record["field_id"] = state.item.get("form_item_id")
        self.agentic_round_states.append(record)
        self.agentic_trace.record(
            str(state.item.get("form_item_id") or ""),
            "controller",
            "round_state",
            {
                "round_index": state.round_index,
                "answer_status": state.prediction.answer_status if state.prediction else None,
                "evidence_state": state.diagnosis.state_kind.value if state.diagnosis else None,
                "evidence_count": len(state.evidence),
                "actions_taken": [action.to_dict() for action in state.actions_taken],
                "stopped_reason": state.stopped_reason,
            },
        )

    def _filter_actions(self, actions: list[EvidenceAction], cfg: Any) -> list[EvidenceAction]:
        filtered = [action for action in actions if action_allowed(action, cfg)]
        if not filtered:
            return []
        return filtered

    def _attach_parent_payloads(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        attach = getattr(self.runner, "attach_parent_payloads", None)
        if attach is None:
            return hits
        return attach(hits)

    def write_optional_artifacts(self, out_dir: Path) -> None:
        if self.mode == "agentic_mas":
            cfg = getattr(getattr(self.runner, "config", None), "agentic_mas", None)
            if cfg is None or cfg.trace.write_agentic_trace:
                self.agentic_trace.write_jsonl(out_dir / "agentic_mas_trace.jsonl")
            if cfg is None or cfg.trace.write_round_states:
                write_jsonl(out_dir / "agentic_round_states.jsonl", self.agentic_round_states)
            write_json(out_dir / "agentic_summary.json", build_agentic_summary(self.agentic_summary_records))
            write_jsonl(
                out_dir / "agentscope_events.jsonl",
                [
                    {
                        "event_type": "runtime_selected",
                        "mode": self.mode,
                        "agentscope_available": self.runtime.available,
                        "agentscope_version": self.runtime.agentscope_version,
                        "fallback_reason": self.runtime.reason,
                    }
                ]
                + self.runtime.events,
            )
            return
        if self.mode not in {"equivalent_mas", "trace_only"}:
            return
        self.trace.write_jsonl(out_dir / "mas_trace.jsonl")
        write_jsonl(
            out_dir / "agentscope_events.jsonl",
            [
                {
                    "event_type": "runtime_selected",
                    "mode": self.mode,
                    "agentscope_available": self.runtime.available,
                    "agentscope_version": self.runtime.agentscope_version,
                    "fallback_reason": self.runtime.reason,
                }
            ]
            + self.runtime.events,
        )

    def record_trace_only_result(self, item: dict[str, Any], result: Any) -> None:
        from nested_doc_rag.agent.step15_runner import field_id_for_item

        field_id = field_id_for_item(item)
        self.trace.record(
            field_id,
            "controller",
            "trace_only_observed",
            {
                "answer_status": result.prediction.answer_status,
                "critic_flags": result.critic_flags,
                "review_required": result.overlay.review_required,
                "writeback_allowed": result.overlay.writeback_allowed,
            },
        )


def _role_payload(item: dict[str, Any], *, stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "field_id": item.get("form_item_id"),
        "row_index": item.get("row_index"),
        "target_cell": item.get("target_cell"),
    }


def _stop_action(state: AgenticMASState, purpose: str) -> EvidenceAction:
    return EvidenceAction(action_type=ActionType.STOP, query_text=state.current_query, purpose=purpose)


def _mark_unresolved_action(state: AgenticMASState, purpose: str) -> EvidenceAction:
    return EvidenceAction(action_type=ActionType.MARK_UNRESOLVED, query_text=state.current_query, purpose=purpose)


def _is_terminal_actions(actions: list[EvidenceAction]) -> bool:
    return bool(actions) and all(action.action_type in {ActionType.STOP, ActionType.MARK_UNRESOLVED} for action in actions)


def _terminal_stopped_reason(actions: list[EvidenceAction], state: AgenticMASState) -> str:
    if any(action.action_type == ActionType.STOP for action in actions):
        if state.diagnosis and state.diagnosis.state_kind == EvidenceStateKind.SUFFICIENT:
            return "sufficient"
        return "stop"
    purpose = actions[0].purpose or "mark unresolved"
    return f"mark_unresolved:{purpose}"


def action_allowed(action: EvidenceAction, cfg: Any) -> bool:
    retrieval_actions = cfg.retrieval_actions
    if action.action_type in {ActionType.STOP, ActionType.MARK_UNRESOLVED}:
        return True
    if action.action_type == ActionType.SLOT_TARGETED_RETRIEVAL:
        return bool(retrieval_actions.allow_slot_targeted)
    if action.action_type == ActionType.ALIAS_RETRIEVAL:
        return bool(retrieval_actions.allow_alias_retrieval)
    if action.action_type == ActionType.LAYER_EXPANSION:
        return bool(retrieval_actions.allow_layer_expansion)
    if action.action_type == ActionType.SOURCE_SPECIFIC_RETRIEVAL:
        return bool(retrieval_actions.allow_source_specific)
    if action.action_type == ActionType.CONTRASTIVE_RETRIEVAL:
        return bool(retrieval_actions.allow_contrastive)
    if action.action_type == ActionType.DISAMBIGUATION_RETRIEVAL:
        return bool(retrieval_actions.allow_disambiguation)
    return False


def workflow_for_action(action: EvidenceAction) -> str | None:
    if action.action_type == ActionType.SLOT_TARGETED_RETRIEVAL:
        return "missing_info"
    if action.action_type in {ActionType.ALIAS_RETRIEVAL, ActionType.LAYER_EXPANSION, ActionType.SOURCE_SPECIFIC_RETRIEVAL}:
        return "not_found_recovery"
    if action.action_type == ActionType.DISAMBIGUATION_RETRIEVAL:
        return "uncertainty_conflict"
    if action.action_type == ActionType.CONTRASTIVE_RETRIEVAL:
        purpose = action.purpose or ""
        return "uncertainty_conflict" if "candidate" in purpose else "wrong_answer_risk"
    return None


def build_agentic_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    triggered: dict[str, int] = {}
    for record in records:
        for workflow in record.get("triggered_workflows") or []:
            triggered[workflow] = triggered.get(workflow, 0) + 1
    return {
        "fields": records,
        "totals": {
            "fields": len(records),
            "rounds": sum(int(record.get("rounds") or 0) for record in records),
            "retrieval_actions": sum(int(record.get("retrieval_actions") or 0) for record in records),
            "novel_chunks": sum(int(record.get("novel_chunks") or 0) for record in records),
            "status_changed": sum(1 for record in records if record.get("status_changed")),
            "answer_changed": sum(1 for record in records if record.get("answer_changed")),
            "triggered_workflows": triggered,
            "stopped_reason": _count_values(str(record.get("stopped_reason") or "") for record in records),
            "initial_status": _count_values(str(record.get("initial_status") or "") for record in records),
            "final_status": _count_values(str(record.get("final_status") or "") for record in records),
        },
    }


def _count_values(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts
