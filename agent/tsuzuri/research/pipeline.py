"""Mode orchestration: run_fast / run_grounded / run_deep / run_research.

This module only sequences components and applies the P2.2 runtime
policies (budgets, degradation, checkpoint restore). Research
semantics live in the component modules.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai.usage import RunUsage

from tsuzuri.research.analysis import (
    assess_research_sufficiency,
    build_evidence_consolidator,
    build_finding_conflicts,
    build_findings,
    build_sufficiency_assessor,
    consolidate_evidence,
)
from tsuzuri.research.evidence import (
    build_evidence_extractor,
    build_source_selector,
    extract_pending_evidence,
)
from tsuzuri.research.models import (
    AnalysisOutcome,
    ResearchMode,
    ResearchSufficiencyStatus,
)
from tsuzuri.research.reporting import write_research_report
from tsuzuri.research.retrieval import (
    build_gap_research_prompt,
    build_research_agent,
    run_research_round,
)
from tsuzuri.research.runtime import (
    LIVE_LOG_PATH,
    RunSignals,
    build_model,
    log_progress,
)
from tsuzuri.research.state import ResearchState
from tsuzuri.research.synthesis import (
    build_deep_synthesizer,
    build_fast_synthesizer,
    build_grounded_synthesizer,
    produce_final_answer,
    synthesize_fast_answer,
)

MAX_RESEARCH_ROUNDS = 1

INITIAL_REQUEST_LIMIT = 4
INITIAL_TOOL_CALLS_LIMIT = 4

GAP_REQUEST_LIMIT = 3
GAP_TOOL_CALLS_LIMIT = 3

FAST_REQUEST_LIMIT = 3
FAST_TOOL_CALLS_LIMIT = 2


@dataclass
class ResearchAgents:
    """All PydanticAI agents used by one research run."""

    research: Any
    selector: Any
    extractor: Any
    consolidator: Any
    sufficiency_assessor: Any
    fast_synthesizer: Any
    grounded_synthesizer: Any
    deep_synthesizer: Any


def build_agents(model) -> ResearchAgents:
    return ResearchAgents(
        research=build_research_agent(model),
        selector=build_source_selector(model),
        extractor=build_evidence_extractor(model),
        consolidator=build_evidence_consolidator(model),
        sufficiency_assessor=build_sufficiency_assessor(model),
        fast_synthesizer=build_fast_synthesizer(model),
        grounded_synthesizer=build_grounded_synthesizer(model),
        deep_synthesizer=build_deep_synthesizer(model),
    )


@dataclass
class ModeOutcome:
    """What one mode run produced, consumed by reporting."""

    final_answer: str | None
    research_round_stop: str | None
    pipeline_stop: str | None
    usage: RunUsage


@dataclass
class ResearchResult:
    """Full result of one research run, returned to the entry point."""

    final_answer: str | None
    research_round_stop: str | None
    pipeline_stop: str | None
    usage: RunUsage
    report_path: Path
    state: ResearchState
    signals: RunSignals


async def run_fast(
    *,
    question: str,
    agents: ResearchAgents,
    state: ResearchState,
    signals: RunSignals,
) -> ModeOutcome:
    """FAST: tiny bounded round; answer directly, or from sources."""
    log_progress("FAST RESEARCH started")

    round_result = await run_research_round(
        agents.research,
        prompt=question,
        research_state=state,
        request_limit=FAST_REQUEST_LIMIT,
        tool_calls_limit=FAST_TOOL_CALLS_LIMIT,
        signals=signals,
    )

    if round_result.result is not None:
        final_answer = str(round_result.result.output)
        signals.final_capability = "agent"
    elif state.sources:
        signals.stage = "fast_synthesis"

        log_progress(
            "FAST RESEARCH budget exhausted; "
            "finalizing from captured sources"
        )

        final_answer = await synthesize_fast_answer(
            agents.fast_synthesizer,
            question=question,
            sources=state.sources,
            signals=signals,
        )

        signals.final_capability = "fast_synthesis"
    else:
        raise RuntimeError(
            "Fast research produced no sources and no answer."
        )

    log_progress(
        f"FAST RESEARCH stopped reason={round_result.stop_reason}"
    )

    return ModeOutcome(
        final_answer=final_answer,
        research_round_stop=round_result.stop_reason,
        pipeline_stop="completed",
        usage=round_result.usage,
    )


async def run_grounded(
    *,
    question: str,
    agents: ResearchAgents,
    state: ResearchState,
    signals: RunSignals,
) -> ModeOutcome:
    """GROUNDED: one bounded round -> evidence -> evidence-only answer."""
    log_progress("GROUNDED RESEARCH started")

    round_result = await run_research_round(
        agents.research,
        prompt=question,
        research_state=state,
        request_limit=INITIAL_REQUEST_LIMIT,
        tool_calls_limit=INITIAL_TOOL_CALLS_LIMIT,
        signals=signals,
    )

    log_progress(
        "GROUNDED RESEARCH stopped "
        f"reason={round_result.stop_reason} "
        f"[requests={round_result.usage.requests}, "
        f"tools={round_result.usage.tool_calls}]"
    )

    signals.stage = "evidence_extraction"

    log_progress("EVIDENCE EXTRACTION started")

    total_evidence = await extract_pending_evidence(
        selector=agents.selector,
        extractor=agents.extractor,
        question=question,
        research_state=state,
        signals=signals,
    )

    log_progress(
        f"EVIDENCE EXTRACTION completed evidence={total_evidence}"
    )

    if not state.evidence:
        raise RuntimeError(
            "Grounded research produced no validated evidence."
        )

    signals.stage = "grounded_synthesis"

    log_progress("GROUNDED SYNTHESIS started")

    final_answer = await produce_final_answer(
        question=question,
        evidence=state.evidence,
        sources=state.sources,
        grounded_synthesizer=agents.grounded_synthesizer,
        deep_synthesizer=agents.deep_synthesizer,
        signals=signals,
    )

    return ModeOutcome(
        final_answer=final_answer,
        research_round_stop=round_result.stop_reason,
        pipeline_stop="completed",
        usage=round_result.usage,
    )


async def run_deep(
    *,
    question: str,
    agents: ResearchAgents,
    state: ResearchState,
    signals: RunSignals,
    analysis: AnalysisOutcome,
) -> ModeOutcome:
    """DEEP: grounded foundation + cross-source analysis + gap rounds."""
    log_progress("INITIAL RESEARCH started")

    round_result = await run_research_round(
        agents.research,
        prompt=question,
        research_state=state,
        request_limit=INITIAL_REQUEST_LIMIT,
        tool_calls_limit=INITIAL_TOOL_CALLS_LIMIT,
        signals=signals,
    )

    log_progress(
        "INITIAL RESEARCH stopped "
        f"reason={round_result.stop_reason} "
        f"[requests={round_result.usage.requests}, "
        f"tools={round_result.usage.tool_calls}]"
    )

    signals.stage = "evidence_extraction"

    log_progress("EVIDENCE EXTRACTION started")

    total_evidence = await extract_pending_evidence(
        selector=agents.selector,
        extractor=agents.extractor,
        question=question,
        research_state=state,
        signals=signals,
    )

    log_progress(
        f"EVIDENCE EXTRACTION completed evidence={total_evidence}"
    )

    await _consolidate_or_degrade(
        question=question,
        agents=agents,
        state=state,
        signals=signals,
        analysis=analysis,
    )

    await _run_gap_rounds(
        question=question,
        agents=agents,
        state=state,
        signals=signals,
        analysis=analysis,
    )

    if not state.evidence:
        raise RuntimeError(
            "Deep research produced no validated evidence."
        )

    if analysis.findings is not None and analysis.sufficiency is not None:
        signals.stage = "deep_synthesis"

        log_progress("DEEP SYNTHESIS started")
    else:
        signals.stage = "grounded_synthesis"

        log_progress("DEEP SYNTHESIS degraded to grounded synthesis")

    final_answer = await produce_final_answer(
        question=question,
        evidence=state.evidence,
        sources=state.sources,
        grounded_synthesizer=agents.grounded_synthesizer,
        deep_synthesizer=agents.deep_synthesizer,
        signals=signals,
        findings=analysis.findings,
        conflicts=analysis.finding_conflicts,
        sufficiency=analysis.sufficiency,
    )

    if analysis.sufficiency is not None:
        pipeline_stop = analysis.sufficiency.status.value
    else:
        pipeline_stop = "degraded"

    return ModeOutcome(
        final_answer=final_answer,
        research_round_stop=round_result.stop_reason,
        pipeline_stop=pipeline_stop,
        usage=round_result.usage,
    )


async def _consolidate_or_degrade(
    *,
    question: str,
    agents: ResearchAgents,
    state: ResearchState,
    signals: RunSignals,
    analysis: AnalysisOutcome,
) -> None:
    """Initial consolidation + sufficiency with per-stage degradation."""
    if state.evidence:
        try:
            signals.stage = "evidence_consolidation"

            log_progress("EVIDENCE CONSOLIDATION started")

            analysis.consolidation = await consolidate_evidence(
                agents.consolidator,
                question=question,
                evidence=state.evidence,
                signals=signals,
            )

            analysis.findings = build_findings(
                consolidation=analysis.consolidation,
                evidence=state.evidence,
            )

            analysis.finding_conflicts = build_finding_conflicts(
                consolidation=analysis.consolidation,
                findings=analysis.findings,
            )

            log_progress(
                "EVIDENCE CONSOLIDATION completed "
                f"groups={len(analysis.consolidation.groups)} "
                f"conflicts={len(analysis.consolidation.conflicts)}"
            )
        except Exception as error:
            log_progress(
                "CONSOLIDATION degraded: "
                f"{type(error).__name__}: {error}; "
                "answering from evidence only"
            )

            signals.warn("evidence_consolidation", error)
    else:
        log_progress("EVIDENCE CONSOLIDATION skipped (no evidence)")

    if analysis.findings is not None:
        try:
            signals.stage = "research_sufficiency"

            log_progress("RESEARCH SUFFICIENCY started")

            analysis.sufficiency = await assess_research_sufficiency(
                agents.sufficiency_assessor,
                question=question,
                findings=analysis.findings,
                conflicts=analysis.finding_conflicts,
                signals=signals,
            )

            log_progress(
                "RESEARCH SUFFICIENCY completed "
                f"status={analysis.sufficiency.status.value} "
                f"gaps={len(analysis.sufficiency.gaps)}"
            )
        except Exception as error:
            log_progress(
                "SUFFICIENCY degraded: "
                f"{type(error).__name__}: {error}; "
                "answering without sufficiency assessment"
            )

            signals.warn("research_sufficiency", error)


async def _run_gap_rounds(
    *,
    question: str,
    agents: ResearchAgents,
    state: ResearchState,
    signals: RunSignals,
    analysis: AnalysisOutcome,
) -> None:
    """Bounded gap-directed research with last-known-good restore."""
    research_round = 0

    while (
        analysis.sufficiency is not None
        and analysis.sufficiency.status
        == ResearchSufficiencyStatus.NEEDS_MORE_RESEARCH
        and research_round < MAX_RESEARCH_ROUNDS
    ):
        research_round += 1

        if not analysis.sufficiency.gaps:
            break

        gap = analysis.sufficiency.gaps[0]

        previous_consolidation = analysis.consolidation
        previous_findings = analysis.findings
        previous_conflicts = analysis.finding_conflicts
        previous_sufficiency = analysis.sufficiency

        try:
            signals.stage = "gap_research"

            log_progress(
                f"GAP RESEARCH round={research_round} kind={gap.kind.value}"
            )

            existing_source_ids = {
                source.id for source in state.sources
            }

            gap_prompt = build_gap_research_prompt(
                question=question,
                gap=gap,
                findings=analysis.findings,
                conflicts=analysis.finding_conflicts,
            )

            gap_round = await run_research_round(
                agents.research,
                prompt=gap_prompt,
                research_state=state,
                request_limit=GAP_REQUEST_LIMIT,
                tool_calls_limit=GAP_TOOL_CALLS_LIMIT,
                signals=signals,
            )

            log_progress(
                f"GAP RESEARCH stopped reason={gap_round.stop_reason}"
            )

            new_sources = [
                source
                for source in state.sources
                if source.id not in existing_source_ids
            ]

            log_progress(
                f"GAP RESEARCH retrieved sources={len(new_sources)}"
            )

            if not new_sources:
                log_progress("GAP RESEARCH stopped: no new sources")
                break

            signals.stage = "gap_evidence_extraction"

            new_evidence = await extract_pending_evidence(
                selector=agents.selector,
                extractor=agents.extractor,
                question=question,
                research_state=state,
                signals=signals,
            )

            log_progress(f"GAP RESEARCH evidence={new_evidence}")

            signals.stage = "gap_consolidation"

            analysis.consolidation = await consolidate_evidence(
                agents.consolidator,
                question=question,
                evidence=state.evidence,
                signals=signals,
            )

            analysis.findings = build_findings(
                consolidation=analysis.consolidation,
                evidence=state.evidence,
            )

            analysis.finding_conflicts = build_finding_conflicts(
                consolidation=analysis.consolidation,
                findings=analysis.findings,
            )

            signals.stage = "research_sufficiency"

            analysis.sufficiency = await assess_research_sufficiency(
                agents.sufficiency_assessor,
                question=question,
                findings=analysis.findings,
                conflicts=analysis.finding_conflicts,
                signals=signals,
            )

            log_progress(
                f"GAP RESEARCH reassessed "
                f"status={analysis.sufficiency.status.value}"
            )
        except Exception as error:
            analysis.consolidation = previous_consolidation
            analysis.findings = previous_findings
            analysis.finding_conflicts = previous_conflicts
            analysis.sufficiency = previous_sufficiency

            log_progress(
                "GAP ENRICHMENT degraded; "
                "restored previous validated state"
            )

            signals.warn(signals.stage, error)

            break


async def run_research(
    *,
    question: str,
    mode: ResearchMode,
) -> ResearchResult:
    """Run one research question in the requested mode, then report.

    Always writes the run report; re-raises fatal errors afterwards.
    """
    wall_started = time.monotonic()

    state = ResearchState()
    signals = RunSignals()
    analysis = AnalysisOutcome()

    LIVE_LOG_PATH.write_text("", encoding="utf-8")

    model = build_model()
    agents = build_agents(model)

    outcome: ModeOutcome | None = None
    run_error: Exception | None = None

    try:
        if mode is ResearchMode.FAST:
            outcome = await run_fast(
                question=question,
                agents=agents,
                state=state,
                signals=signals,
            )
        elif mode is ResearchMode.GROUNDED:
            outcome = await run_grounded(
                question=question,
                agents=agents,
                state=state,
                signals=signals,
            )
        else:
            outcome = await run_deep(
                question=question,
                agents=agents,
                state=state,
                signals=signals,
                analysis=analysis,
            )
    except Exception as error:
        run_error = error

        log_progress(
            f"RUN FAILED in {signals.stage}: "
            f"{type(error).__name__}: {error}"
        )

    finally:
        signals.metrics.wall_seconds = time.monotonic() - wall_started

        report_path = write_research_report(
            final_answer=outcome.final_answer if outcome else None,
            usage=outcome.usage if outcome else RunUsage(),
            research_state=state,
            consolidation=analysis.consolidation,
            findings=analysis.findings,
            finding_conflicts=analysis.finding_conflicts,
            sufficiency=analysis.sufficiency,
            error=run_error,
            stage=signals.stage,
            mode=mode.value,
            warnings=signals.warnings,
            final_capability=signals.final_capability,
            metrics=signals.metrics,
            research_round_stop=(
                outcome.research_round_stop if outcome else None
            ),
            pipeline_stop=outcome.pipeline_stop if outcome else None,
        )

        log_progress(f"REPORT written: {report_path}")

    if run_error is not None:
        raise run_error

    return ResearchResult(
        final_answer=outcome.final_answer,
        research_round_stop=outcome.research_round_stop,
        pipeline_stop=outcome.pipeline_stop,
        usage=outcome.usage,
        report_path=report_path,
        state=state,
        signals=signals,
    )
