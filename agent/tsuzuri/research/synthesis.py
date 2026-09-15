"""Final answer synthesis.

Capability ladder with graceful degradation:

    deep synthesis (full validated research state)
      -> grounded synthesis (validated evidence only)
      -> deterministic evidence rendering

The model writes semantic text and chooses evidence IDs; the runtime
validates those IDs and renders citation markers. Evidence blocks
carry source metadata so the synthesizer can name sources without
guessing their identity.
"""

from pydantic_ai import Agent, ModelRetry, RunContext

from tsuzuri.research.models import (
    Evidence,
    FinalSynthesis,
    Finding,
    FindingConflict,
    GroundedSynthesisDeps,
    ResearchSufficiency,
    SourceCandidate,
)
from tsuzuri.research.runtime import (
    RunSignals,
    log_progress,
    run_model_call,
    source_content_to_text,
)


def validate_grounded_synthesis(
    ctx: RunContext[GroundedSynthesisDeps],
    output: FinalSynthesis,
) -> FinalSynthesis:
    for statement in output.statements:
        unknown = set(statement.evidence_ids) - ctx.deps.evidence_ids

        if unknown:
            raise ModelRetry(f"Unknown evidence IDs: {sorted(unknown)}")

    return output


def build_grounded_synthesizer(model) -> Agent[GroundedSynthesisDeps, FinalSynthesis]:
    synthesizer: Agent[GroundedSynthesisDeps, FinalSynthesis] = Agent(
        model,
        deps_type=GroundedSynthesisDeps,
        output_type=FinalSynthesis,
        instructions="""
You are a grounded answer synthesizer.

You receive:
- one user question;
- validated evidence records.

Answer the user's question using only the supplied evidence.

Rules:
- Every statement must cite one or more supplied evidence IDs.
- Every factual statement must be fully supported by its cited evidence.
- You may compare evidence and explicitly state that sources disagree
  when the cited evidence contains conflicting values.
- For comparison or conflict statements, cite evidence supporting
  every side of the comparison. A statement describing A vs B must
  include evidence for both A and B.
- Do not explain or reconcile a conflict unless the supplied evidence
  explicitly supports that explanation.
- Do not use background knowledge.
- Do not invent missing facts.
- Do not convert dates, times, time zones, currencies, units,
  or derive new numeric values unless the transformed value is
  explicitly present in the supplied evidence.
- Do not add deterministic-looking derived facts on your own.
- Prefer a concise direct answer.
- Omit information that does not materially help answer the question.
- Use statements for externally factual claims only. Put
  meta-information about this research (missing sources, coverage
  limits, steps that could not be completed) in limitations;
  limitations need no evidence citations.
""".strip(),
    )

    synthesizer.output_validator(validate_grounded_synthesis)

    return synthesizer


def build_grounded_synthesis_prompt(
    *,
    question: str,
    evidence: list[Evidence],
    sources: list[SourceCandidate],
) -> str:
    blocks = []

    source_by_id = {source.id: source for source in sources}

    for item in evidence:
        source = source_by_id[item.source_candidate_id]

        blocks.append(
            f"""
<EVIDENCE id="{item.id}">
claim: {item.claim}
support: {item.support}
source_name: {source.source_name}
source_url: {source.source_url or "-"}
source_kind: {source.kind.value}
</EVIDENCE>
""".strip()
        )

    return f"""
USER QUESTION:
{question}

VALIDATED EVIDENCE:

{"\n\n".join(blocks)}
""".strip()


async def synthesize_grounded_answer(
    grounded_synthesizer: Agent[GroundedSynthesisDeps, FinalSynthesis],
    *,
    question: str,
    evidence: list[Evidence],
    sources: list[SourceCandidate],
    signals: RunSignals,
) -> FinalSynthesis:
    deps = GroundedSynthesisDeps(
        evidence_ids={item.id for item in evidence},
    )

    prompt = build_grounded_synthesis_prompt(
        question=question,
        evidence=evidence,
        sources=sources,
    )

    return await run_model_call(
        grounded_synthesizer,
        label="SYNTHESIZER",
        prompt=prompt,
        signals=signals,
        deps=deps,
    )


def validate_deep_synthesis(
    ctx: RunContext[GroundedSynthesisDeps],
    output: FinalSynthesis,
) -> FinalSynthesis:
    for statement in output.statements:
        unknown = set(statement.evidence_ids) - ctx.deps.evidence_ids

        if unknown:
            raise ModelRetry(f"Unknown evidence IDs: {sorted(unknown)}")

    return output


def build_deep_synthesizer(model) -> Agent[GroundedSynthesisDeps, FinalSynthesis]:
    synthesizer: Agent[GroundedSynthesisDeps, FinalSynthesis] = Agent(
        model,
        deps_type=GroundedSynthesisDeps,
        output_type=FinalSynthesis,
        instructions="""
You are the final answer synthesizer for a deep research run.

Use only the supplied validated research state.

Rules:
- Every factual statement must cite one or more supplied evidence IDs.
- Treat Findings as the consolidated factual structure.
- Explicitly preserve conflicts that materially affect the answer.
- For comparison or conflict statements, cite evidence supporting
  every side of the comparison. A statement describing A vs B must
  include evidence for both A and B.
- Never reconcile a conflict unless the supplied evidence explicitly
  supports that reconciliation.
- If research sufficiency is NEEDS_MORE_RESEARCH, clearly state what
  remains unresolved instead of pretending the question is settled.
- Do not use background knowledge.
- Do not invent missing facts.
- Do not expose internal finding IDs or conflict IDs in user-facing text.
- Do not convert dates, times, time zones, currencies, units,
  or derive new numeric values unless the transformed value is
  explicitly present in the supplied evidence.
- Do not add deterministic-looking derived facts on your own.
- Prefer a concise direct answer.
- Use statements for externally factual claims only. Put
  meta-information about the research state (unresolved gaps,
  coverage limits, steps that could not be completed) in
  limitations; limitations need no evidence citations.
""".strip(),
    )

    synthesizer.output_validator(validate_deep_synthesis)

    return synthesizer


def build_deep_synthesis_prompt(
    *,
    question: str,
    evidence: list[Evidence],
    sources: list[SourceCandidate],
    findings: list[Finding],
    conflicts: list[FindingConflict],
    sufficiency: ResearchSufficiency,
) -> str:
    evidence_blocks = []

    source_by_id = {source.id: source for source in sources}

    for item in evidence:
        source = source_by_id[item.source_candidate_id]

        evidence_blocks.append(
            f"""
<EVIDENCE id="{item.id}">
claim: {item.claim}
support: {item.support}
source_name: {source.source_name}
source_url: {source.source_url or "-"}
source_kind: {source.kind.value}
</EVIDENCE>
""".strip()
        )

    finding_blocks = []

    for finding in findings:
        finding_blocks.append(
            f"""
<FINDING id="{finding.id}">
claim: {finding.claim}
evidence_ids: {", ".join(finding.evidence_ids)}
</FINDING>
""".strip()
        )

    conflict_blocks = []

    for conflict in conflicts:
        conflict_blocks.append(
            f"""
<CONFLICT id="{conflict.id}">
left: {conflict.left_finding_id}
right: {conflict.right_finding_id}
</CONFLICT>
""".strip()
        )

    return f"""
USER QUESTION:
{question}

VALIDATED EVIDENCE:

{"\n\n".join(evidence_blocks)}

CONSOLIDATED FINDINGS:

{"\n\n".join(finding_blocks)}

UNRESOLVED CONFLICTS:

{"\n\n".join(conflict_blocks) if conflict_blocks else "(none)"}

RESEARCH SUFFICIENCY:

{sufficiency.status.value}
""".strip()


async def synthesize_deep_answer(
    deep_synthesizer: Agent[GroundedSynthesisDeps, FinalSynthesis],
    *,
    question: str,
    evidence: list[Evidence],
    sources: list[SourceCandidate],
    findings: list[Finding],
    conflicts: list[FindingConflict],
    sufficiency: ResearchSufficiency,
    signals: RunSignals,
) -> FinalSynthesis:
    deps = GroundedSynthesisDeps(
        evidence_ids={item.id for item in evidence},
    )

    prompt = build_deep_synthesis_prompt(
        question=question,
        evidence=evidence,
        sources=sources,
        findings=findings,
        conflicts=conflicts,
        sufficiency=sufficiency,
    )

    return await run_model_call(
        deep_synthesizer,
        label="DEEP SYNTHESIZER",
        prompt=prompt,
        signals=signals,
        deps=deps,
    )


def build_fast_synthesizer(model) -> Agent[None, str]:
    synthesizer: Agent[None, str] = Agent(
        model,
        output_type=str,
        instructions="""
You are a fast answer synthesizer.

You receive one user question and the source records gathered by a
research agent that ran out of its tool budget before writing a
final answer.

Answer the user's question using the supplied sources.

Rules:
- Base factual statements on the supplied sources.
- If sources disagree, say so explicitly.
- Do not use background knowledge.
- Do not invent missing facts.
- Do not convert dates, times, time zones, currencies, units,
  or derive new numeric values unless the transformed value is
  explicitly present in the supplied sources.
- Do not add deterministic-looking derived facts on your own.
- Prefer a concise direct answer.
""".strip(),
    )

    return synthesizer


def build_fast_synthesis_prompt(
    *,
    question: str,
    sources: list[SourceCandidate],
) -> str:
    source_blocks = []

    for source in sources:
        source_blocks.append(
            f"""
<SOURCE id="{source.id}">
kind: {source.kind.value}
name: {source.source_name}
url: {source.source_url or "-"}

CONTENT:
{source_content_to_text(source.content)}
</SOURCE>
""".strip()
        )

    return f"""
USER QUESTION:
{question}

SOURCES:

{"\n\n".join(source_blocks)}
""".strip()


async def synthesize_fast_answer(
    fast_synthesizer: Agent[None, str],
    *,
    question: str,
    sources: list[SourceCandidate],
    signals: RunSignals,
) -> str:
    prompt = build_fast_synthesis_prompt(
        question=question,
        sources=sources,
    )

    return await run_model_call(
        fast_synthesizer,
        label="FAST SYNTHESIZER",
        prompt=prompt,
        signals=signals,
    )


def render_final_answer(
    synthesis: FinalSynthesis,
) -> str:
    lines: list[str] = []

    for statement in synthesis.statements:
        refs = " ".join(
            f"[{evidence_id}]" for evidence_id in statement.evidence_ids
        )

        lines.append(f"{statement.text} {refs}")

    if synthesis.limitations:
        lines.append("")
        lines.append("Limitations:")
        lines.append("")

        for limitation in synthesis.limitations:
            lines.append(f"- {limitation}")

    return "\n".join(lines)


def render_evidence_fallback(
    evidence: list[Evidence],
) -> str:
    lines = []

    for item in evidence[:5]:
        lines.append(f"{item.claim} [{item.id}]")

    return "\n\n".join(lines)


async def produce_final_answer(
    *,
    question: str,
    evidence: list[Evidence],
    sources: list[SourceCandidate],
    grounded_synthesizer: Agent[GroundedSynthesisDeps, FinalSynthesis],
    deep_synthesizer: Agent[GroundedSynthesisDeps, FinalSynthesis],
    signals: RunSignals,
    findings: list[Finding] | None = None,
    conflicts: list[FindingConflict] | None = None,
    sufficiency: ResearchSufficiency | None = None,
) -> str:
    """Render the final answer with a graceful fallback ladder.

    deep synthesis (full research state) -> grounded synthesis
    -> deterministic evidence rendering.
    """
    if (
        findings is not None
        and conflicts is not None
        and sufficiency is not None
    ):
        try:
            synthesis = await synthesize_deep_answer(
                deep_synthesizer,
                question=question,
                evidence=evidence,
                sources=sources,
                findings=findings,
                conflicts=conflicts,
                sufficiency=sufficiency,
                signals=signals,
            )

            log_progress(
                "DEEP SYNTHESIS completed "
                f"statements={len(synthesis.statements)}"
            )

            signals.final_capability = "deep"

            return render_final_answer(synthesis)
        except Exception as error:
            log_progress(
                "DEEP SYNTHESIS degraded: "
                f"{type(error).__name__}: {error}; "
                "falling back to grounded synthesis"
            )

            signals.warn("deep_synthesis", error)

    try:
        synthesis = await synthesize_grounded_answer(
            grounded_synthesizer,
            question=question,
            evidence=evidence,
            sources=sources,
            signals=signals,
        )

        log_progress(
            "GROUNDED SYNTHESIS completed "
            f"statements={len(synthesis.statements)}"
        )

        signals.final_capability = "grounded"

        return render_final_answer(synthesis)
    except Exception as error:
        log_progress(
            "GROUNDED SYNTHESIS degraded: "
            f"{type(error).__name__}: {error}; "
            "using deterministic evidence rendering"
        )

        signals.warn("grounded_synthesis", error)

        signals.final_capability = "deterministic"

        return render_evidence_fallback(evidence)
