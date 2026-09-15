"""Run report and value formatting for research_run.md."""

from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_ai.usage import RunUsage

from tsuzuri.research.models import (
    EvidenceConsolidation,
    Finding,
    FindingConflict,
    ObservationStatus,
    PipelineMetrics,
    PipelineWarning,
    ResearchSufficiency,
)
from tsuzuri.research.runtime import REPORT_PATH
from tsuzuri.research.state import ResearchState


def format_value(value: Any) -> str:
    """Format runtime values for a human-readable Markdown report."""
    if value is None:
        return "null"

    if isinstance(value, BaseModel):
        return value.model_dump_json(indent=2)

    if isinstance(value, Enum):
        return str(value.value)

    if is_dataclass(value):
        data = {
            field.name: getattr(value, field.name)
            for field in fields(value)
            if not field.name.startswith("_")
        }
        return repr(data)

    return repr(value)


def write_research_report(
    *,
    final_answer: str | None,
    usage: RunUsage,
    research_state: ResearchState,
    consolidation: EvidenceConsolidation | None = None,
    findings: list[Finding] | None = None,
    finding_conflicts: list[FindingConflict] | None = None,
    sufficiency: ResearchSufficiency | None = None,
    error: Exception | None = None,
    stage: str | None = None,
    mode: str | None = None,
    warnings: list[PipelineWarning] | None = None,
    final_capability: str | None = None,
    metrics: PipelineMetrics | None = None,
    research_round_stop: str | None = None,
    pipeline_stop: str | None = None,
) -> Path:
    output_path = REPORT_PATH

    lines: list[str] = []

    lines.append("# Tsuzuri Research Run")
    lines.append("")

    # Pipeline status
    status = "success"

    if error is not None:
        status = "failed"
    elif warnings:
        status = "degraded"

    if sufficiency is not None:
        checkpoint = "sufficiency"
    elif findings:
        checkpoint = "findings"
    elif research_state.evidence:
        checkpoint = "evidence"
    elif research_state.sources:
        checkpoint = "sources"
    else:
        checkpoint = "none"

    lines.append("## Pipeline Status")
    lines.append("")

    if mode is not None:
        lines.append(f"- Requested Mode: `{mode}`")

    lines.append(f"- Status: `{status}`")

    if research_round_stop is not None:
        lines.append(f"- Research Round Stop: `{research_round_stop}`")

    if pipeline_stop is not None:
        lines.append(f"- Pipeline Stop: `{pipeline_stop}`")

    if final_capability is not None:
        lines.append(f"- Final Capability: `{final_capability}`")

    lines.append(f"- Last Validated Checkpoint: `{checkpoint}`")

    if warnings:
        lines.append("")
        lines.append("**Warnings**")
        lines.append("")

        for warning in warnings:
            lines.append(
                f"- {warning.stage}: {warning.error_type}: {warning.message}"
            )

    lines.append("")

    # Final answer
    lines.append("## Final Answer")
    lines.append("")

    if final_answer is None:
        lines.append("_Run did not complete._")
    else:
        lines.append(final_answer)

    lines.append("")

    # Usage
    lines.append("## Usage")
    lines.append("")
    lines.append("```text")
    lines.append(str(usage))
    lines.append("```")
    lines.append("")

    # Pipeline metrics
    if metrics is not None:
        lines.append("## Pipeline Metrics")
        lines.append("")
        lines.append("```text")
        lines.append(
            f"model_requests={metrics.model_requests} "
            f"tool_calls={metrics.tool_calls} "
            f"input_tokens={metrics.input_tokens} "
            f"output_tokens={metrics.output_tokens} "
            f"cost={metrics.cost:.6f} "
            f"model_time={metrics.elapsed_seconds:.1f}s "
            f"wall_time={metrics.wall_seconds:.1f}s"
        )
        lines.append("```")
        lines.append("")

    if error is not None:
        lines.append("## Run Error")
        lines.append("")

        if stage is not None:
            lines.append(f"- Stage: `{stage}`")
            lines.append("")

        lines.append("```text")
        lines.append(f"{type(error).__name__}: {error}")
        lines.append("```")
        lines.append("")

    # Observations
    lines.append("## Observations")
    lines.append("")

    for observation in research_state.observations:
        lines.append(
            f"### {observation.id} — "
            f"`{observation.tool_name}` — "
            f"`{observation.status.value}`"
        )
        lines.append("")

        lines.append("**Arguments**")
        lines.append("")
        lines.append("```text")
        lines.append(format_value(observation.arguments))
        lines.append("```")
        lines.append("")

        if observation.status == ObservationStatus.SUCCESS:
            lines.append("<details>")
            lines.append("<summary>Result</summary>")
            lines.append("")
            lines.append("```text")
            lines.append(format_value(observation.result))
            lines.append("```")
            lines.append("")
            lines.append("</details>")
        else:
            lines.append(f"**Error:** `{observation.error}`")

        lines.append("")

    # Sources
    lines.append("## Source Candidates")
    lines.append("")

    for source in research_state.sources:
        lines.append(f"### {source.id} — `{source.kind.value}`")
        lines.append("")
        lines.append(f"- Observation: `{source.observation_id}`")
        lines.append(f"- Name: {source.source_name}")
        lines.append(f"- URL: {source.source_url or '-'}")
        lines.append(f"- Source ID: `{source.source_id or '-'}`")
        lines.append(f"- Content type: `{type(source.content).__name__}`")
        lines.append("")

        lines.append("<details>")
        lines.append("<summary>Content</summary>")
        lines.append("")
        lines.append("```text")
        lines.append(format_value(source.content))
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Evidence
    lines.append("## Evidence")
    lines.append("")

    if not research_state.evidence:
        lines.append("_No evidence extracted._")
        lines.append("")
    else:
        for evidence in research_state.evidence:
            lines.append(f"### {evidence.id}")
            lines.append("")
            lines.append(
                f"- Source Candidate: `{evidence.source_candidate_id}`"
            )
            lines.append(f"- Observation: `{evidence.observation_id}`")
            lines.append("")
            lines.append("**Claim**")
            lines.append("")
            lines.append(evidence.claim)
            lines.append("")
            lines.append("**Support**")
            lines.append("")
            lines.append("```text")
            lines.append(evidence.support)
            lines.append("```")
            lines.append("")

    # Findings
    if consolidation is not None and findings is not None:
        lines.append("## Findings")
        lines.append("")

        if not findings:
            lines.append("_No findings consolidated._")
            lines.append("")
        else:
            for finding in findings:
                lines.append(f"### {finding.id}")
                lines.append("")
                evidence_refs = ", ".join(
                    f"`{evidence_id}`" for evidence_id in finding.evidence_ids
                )
                lines.append(f"- Evidence: {evidence_refs}")
                lines.append("")
                lines.append("**Claim**")
                lines.append("")
                lines.append(finding.claim)
                lines.append("")

        lines.append("## Conflicts")
        lines.append("")

        if not consolidation.conflicts:
            lines.append("_No conflicts detected._")
            lines.append("")
        else:
            for conflict_index, conflict in enumerate(consolidation.conflicts):
                lines.append(f"### conflict_{conflict_index + 1}")
                lines.append("")
                sides = " ↔ ".join(
                    f"`finding_{group_index + 1}`"
                    for group_index in (
                        conflict.left_group_index,
                        conflict.right_group_index,
                    )
                )
                lines.append(f"- Groups: {sides}")
                lines.append("")

    # Research sufficiency
    if sufficiency is not None:
        lines.append("## Research Sufficiency")
        lines.append("")

        lines.append(f"- Status: `{sufficiency.status.value}`")
        lines.append("")

        if sufficiency.supporting_finding_ids:
            findings_text = ", ".join(
                f"`{finding_id}`"
                for finding_id in sufficiency.supporting_finding_ids
            )

            lines.append(f"- Supporting Findings: {findings_text}")

        if sufficiency.unresolved_conflict_ids:
            conflicts_text = ", ".join(
                f"`{conflict_id}`"
                for conflict_id in sufficiency.unresolved_conflict_ids
            )

            lines.append(f"- Unresolved Conflicts: {conflicts_text}")

        lines.append("")

        if sufficiency.gaps:
            lines.append("### Research Gaps")
            lines.append("")

            for index, gap in enumerate(
                sufficiency.gaps,
                start=1,
            ):
                lines.append(f"#### gap_{index} — `{gap.kind.value}`")
                lines.append("")
                lines.append(gap.description)
                lines.append("")

                if gap.related_finding_ids:
                    refs = ", ".join(
                        f"`{item}`" for item in gap.related_finding_ids
                    )
                    lines.append(f"- Findings: {refs}")

                if gap.related_conflict_ids:
                    refs = ", ".join(
                        f"`{item}`" for item in gap.related_conflict_ids
                    )
                    lines.append(f"- Conflicts: {refs}")

                lines.append("")

    output_path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    return output_path
