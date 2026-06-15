from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schemas import EvidenceScoutReport, QueryPlan, SupplementalRetrievalPlan


@dataclass(frozen=True)
class SupplementalGateConfig:
    enabled: bool
    mode: str
    max_supplemental_rounds: int
    supplemental_enabled_statuses: list[str]
    allow_supplemental_on_answered: bool


class SupplementalRetrievalGate:
    """Deterministic gate for enhanced MAS supplemental retrieval.

    Agent roles may suggest missing evidence and candidate queries, but this gate
    is the only place that decides whether a supplemental retrieval can run.
    """

    def __init__(self, config: Any) -> None:
        self.config = SupplementalGateConfig(
            enabled=bool(getattr(config, "enabled", False)),
            mode=str(getattr(config, "mode", "off")),
            max_supplemental_rounds=max(0, int(getattr(config, "max_supplemental_rounds", 1) or 0)),
            supplemental_enabled_statuses=[
                str(item) for item in list(getattr(config, "supplemental_enabled_statuses", ["partial_clue", "not_found"]) or [])
            ],
            allow_supplemental_on_answered=bool(getattr(config, "allow_supplemental_on_answered", False)),
        )

    def plan(
        self,
        *,
        item: dict[str, Any],
        query_plan: QueryPlan,
        scout_report: EvidenceScoutReport,
        baseline_status: str,
        baseline_critic_flags: list[str] | None = None,
    ) -> SupplementalRetrievalPlan:
        field_id = str(item.get("form_item_id") or f"row_{item.get('row_index')}")
        if not self.config.enabled or self.config.mode != "enhanced_mas":
            return self._disabled(field_id, "enhanced_mas_disabled")
        if self.config.max_supplemental_rounds < 1:
            return self._disabled(field_id, "max_supplemental_rounds_zero")
        if baseline_status == "answered" and not self.config.allow_supplemental_on_answered:
            return self._disabled(field_id, "baseline_answered")

        baseline_critic_flags = baseline_critic_flags or []
        status_allows = baseline_status in set(self.config.supplemental_enabled_statuses)
        evidence_gap = not scout_report.evidence_sufficient or bool(scout_report.missing_slots)
        if not status_allows and not evidence_gap:
            return self._disabled(field_id, "baseline_status_not_eligible")
        if baseline_status == "answered" and not baseline_critic_flags and not self.config.allow_supplemental_on_answered:
            return self._disabled(field_id, "answered_without_critical_flags")

        queries = dedupe_queries(
            [
                *scout_report.supplemental_queries,
                *query_plan.fallback_queries,
            ],
            primary_query=query_plan.primary_query,
        )
        if not queries:
            return self._disabled(field_id, "no_supplemental_queries")
        return SupplementalRetrievalPlan(
            field_id=field_id,
            enabled=True,
            reason="evidence_gap" if evidence_gap else f"baseline_status:{baseline_status}",
            queries=queries,
            rounds=min(1, self.config.max_supplemental_rounds),
        )

    @staticmethod
    def _disabled(field_id: str, reason: str) -> SupplementalRetrievalPlan:
        return SupplementalRetrievalPlan(field_id=field_id, enabled=False, reason=reason, queries=[], rounds=0)


def dedupe_queries(queries: list[str], *, primary_query: str) -> list[str]:
    output: list[str] = []
    seen: set[str] = {primary_query.strip()}
    for query in queries:
        normalized = str(query or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output
