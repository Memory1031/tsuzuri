"""Cross-source analysis: consolidation, findings, conflicts, sufficiency.

The consolidator model only groups evidence IDs and marks conflicts;
it never writes new factual text. The runtime validates the grouping
is a real partition and derives Finding claims verbatim from the
representative evidence. The sufficiency assessor decides whether the
research state is enough to answer the question responsibly.
"""

from pydantic_ai import Agent, ModelRetry, RunContext

from tsuzuri.research.models import (
    Evidence,
    EvidenceConsolidation,
    EvidenceConsolidatorDeps,
    Finding,
    FindingConflict,
    ResearchSufficiency,
    ResearchSufficiencyStatus,
    SufficiencyAssessorDeps,
)
from tsuzuri.research.runtime import RunSignals, run_model_call


def validate_consolidation(
    ctx: RunContext[EvidenceConsolidatorDeps],
    output: EvidenceConsolidation,
) -> EvidenceConsolidation:
    known_ids = ctx.deps.evidence_ids
    assigned_ids: list[str] = []

    for group in output.groups:
        ids = set(group.evidence_ids)

        if not ids <= known_ids:
            raise ModelRetry("All evidence IDs must be provided evidence IDs.")

        if group.representative_evidence_id not in ids:
            raise ModelRetry(
                "representative_evidence_id must also appear in evidence_ids."
            )

        assigned_ids.extend(group.evidence_ids)

    if len(assigned_ids) != len(set(assigned_ids)):
        raise ModelRetry("Each evidence ID must belong to exactly one group.")

    if set(assigned_ids) != known_ids:
        missing = known_ids - set(assigned_ids)

        raise ModelRetry(
            "Every provided evidence record must belong "
            f"to exactly one group. Missing: {sorted(missing)}"
        )

    for conflict in output.conflicts:
        count = len(output.groups)

        if not (
            0 <= conflict.left_group_index < count
            and 0 <= conflict.right_group_index < count
        ):
            raise ModelRetry("Conflict group indexes are invalid.")

        if conflict.left_group_index == conflict.right_group_index:
            raise ModelRetry("A group cannot conflict with itself.")

    return output


def build_evidence_consolidator(
    model,
) -> Agent[EvidenceConsolidatorDeps, EvidenceConsolidation]:
    consolidator: Agent[EvidenceConsolidatorDeps, EvidenceConsolidation] = Agent(
        model,
        deps_type=EvidenceConsolidatorDeps,
        output_type=EvidenceConsolidation,
        instructions="""
You are an evidence grouping component.

You receive already validated evidence records.

Your job is only to:

1. group evidence records that support substantially the same
   factual proposition;
2. keep materially different propositions in separate groups;
3. identify groups whose propositions conflict.

Rules:
- Do not write new factual claims.
- Do not explain or reconcile conflicts.
- Do not infer causes, timezone explanations, motivations,
  or missing facts.
- Do not merge claims merely because they could possibly
  be reconciled.
- If one source says September 8 and another says September 9,
  keep them separate unless the supplied evidence itself
  explicitly establishes that they represent the same date.
- Every evidence record in a group must independently support
  the full factual proposition expressed by the representative
  evidence claim.
- If some evidence supports only a broader or less specific claim,
  either choose a broader representative claim or keep the more
  specific evidence in a separate group.
- Choose one existing evidence record as the representative
  for each group.
- Use only evidence IDs provided in the input.
""".strip(),
    )

    consolidator.output_validator(validate_consolidation)

    return consolidator


def build_evidence_consolidation_prompt(
    *,
    question: str,
    evidence: list[Evidence],
) -> str:
    blocks: list[str] = []

    for item in evidence:
        blocks.append(
            f"""
<EVIDENCE id="{item.id}">
claim: {item.claim}
support: {item.support}
source_candidate_id: {item.source_candidate_id}
</EVIDENCE>
""".strip()
        )

    return f"""
RESEARCH QUESTION:
{question}

EVIDENCE:

{"\n\n".join(blocks)}
""".strip()


async def consolidate_evidence(
    consolidator: Agent[EvidenceConsolidatorDeps, EvidenceConsolidation],
    *,
    question: str,
    evidence: list[Evidence],
    signals: RunSignals,
) -> EvidenceConsolidation:
    deps = EvidenceConsolidatorDeps(
        evidence_ids={item.id for item in evidence},
    )

    prompt = build_evidence_consolidation_prompt(
        question=question,
        evidence=evidence,
    )

    return await run_model_call(
        consolidator,
        label="CONSOLIDATOR",
        prompt=prompt,
        signals=signals,
        deps=deps,
    )


def build_findings(
    *,
    consolidation: EvidenceConsolidation,
    evidence: list[Evidence],
) -> list[Finding]:
    evidence_by_id = {item.id: item for item in evidence}

    findings: list[Finding] = []

    for index, group in enumerate(
        consolidation.groups,
        start=1,
    ):
        representative = evidence_by_id[group.representative_evidence_id]

        findings.append(
            Finding(
                id=f"finding_{index}",
                claim=representative.claim,
                evidence_ids=group.evidence_ids,
            )
        )

    return findings


def build_finding_conflicts(
    *,
    consolidation: EvidenceConsolidation,
    findings: list[Finding],
) -> list[FindingConflict]:
    conflicts: list[FindingConflict] = []

    for index, conflict in enumerate(
        consolidation.conflicts,
        start=1,
    ):
        conflicts.append(
            FindingConflict(
                id=f"conflict_{index}",
                left_finding_id=findings[conflict.left_group_index].id,
                right_finding_id=findings[conflict.right_group_index].id,
            )
        )

    return conflicts


def validate_sufficiency(
    ctx: RunContext[SufficiencyAssessorDeps],
    output: ResearchSufficiency,
) -> ResearchSufficiency:
    finding_ids = ctx.deps.finding_ids
    conflict_ids = ctx.deps.conflict_ids

    unknown_findings = set(output.supporting_finding_ids) - finding_ids

    if unknown_findings:
        raise ModelRetry(
            f"Unknown supporting finding IDs: {sorted(unknown_findings)}"
        )

    unknown_conflicts = set(output.unresolved_conflict_ids) - conflict_ids

    if unknown_conflicts:
        raise ModelRetry(f"Unknown conflict IDs: {sorted(unknown_conflicts)}")

    for gap in output.gaps:
        unknown_gap_findings = set(gap.related_finding_ids) - finding_ids

        if unknown_gap_findings:
            raise ModelRetry(
                "Unknown finding IDs in research gap: "
                f"{sorted(unknown_gap_findings)}"
            )

        unknown_gap_conflicts = set(gap.related_conflict_ids) - conflict_ids

        if unknown_gap_conflicts:
            raise ModelRetry(
                "Unknown conflict IDs in research gap: "
                f"{sorted(unknown_gap_conflicts)}"
            )

    if output.status == ResearchSufficiencyStatus.SUFFICIENT:
        if output.unresolved_conflict_ids:
            raise ModelRetry(
                "SUFFICIENT cannot contain unresolved conflicts. "
                "Use SUFFICIENT_WITH_CONFLICT if the answer can "
                "still be given while preserving the conflict."
            )

        if output.gaps:
            raise ModelRetry("SUFFICIENT cannot contain research gaps.")

    elif output.status == ResearchSufficiencyStatus.SUFFICIENT_WITH_CONFLICT:
        if not output.unresolved_conflict_ids:
            raise ModelRetry(
                "SUFFICIENT_WITH_CONFLICT requires at least "
                "one unresolved conflict."
            )

    elif output.status == ResearchSufficiencyStatus.NEEDS_MORE_RESEARCH:
        if not output.gaps:
            raise ModelRetry(
                "NEEDS_MORE_RESEARCH requires at least "
                "one specific research gap."
            )

    return output


def build_sufficiency_assessor(
    model,
) -> Agent[SufficiencyAssessorDeps, ResearchSufficiency]:
    assessor: Agent[SufficiencyAssessorDeps, ResearchSufficiency] = Agent(
        model,
        deps_type=SufficiencyAssessorDeps,
        output_type=ResearchSufficiency,
        instructions="""
You are a research sufficiency assessor.

You receive:
- one research question;
- already validated findings;
- known unresolved conflicts.

Your only job is to decide whether the existing research state is
sufficient to answer the user's question responsibly.

Status meanings:

SUFFICIENT:
The question can be answered from the supplied findings without
material unresolved uncertainty.

SUFFICIENT_WITH_CONFLICT:
The question can still be answered responsibly, but the final answer
must explicitly preserve one or more unresolved conflicts.
Further research may improve the answer but is not required.

NEEDS_MORE_RESEARCH:
A specific missing fact or unresolved conflict prevents the question
from being answered responsibly.

Rules:
- Do not answer the research question.
- Do not invent new facts.
- Do not use background knowledge.
- Do not explain or reconcile conflicts.
- A conflict does not automatically mean more research is required.
- Judge sufficiency relative to the exact wording and precision of
  the user's question.
- If more research is required, identify a specific information gap.
- Do not use generic gaps such as "find more sources".
- Return the smallest non-overlapping set of research gaps.
- If missing source coverage is intended to investigate an existing
  conflict, prefer one gap that references that conflict instead of
  returning a second redundant unresolved-conflict gap.
- Do not claim that retrieving a missing source will necessarily
  resolve a conflict; describe what information is needed to
  clarify or adjudicate it.
- Use only finding IDs and conflict IDs supplied in the input.
""".strip(),
    )

    assessor.output_validator(validate_sufficiency)

    return assessor


def build_sufficiency_prompt(
    *,
    question: str,
    findings: list[Finding],
    conflicts: list[FindingConflict],
) -> str:
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
RESEARCH QUESTION:
{question}

FINDINGS:

{"\n\n".join(finding_blocks)}

CONFLICTS:

{"\n\n".join(conflict_blocks) if conflict_blocks else "(none)"}
""".strip()


async def assess_research_sufficiency(
    assessor: Agent[SufficiencyAssessorDeps, ResearchSufficiency],
    *,
    question: str,
    findings: list[Finding],
    conflicts: list[FindingConflict],
    signals: RunSignals,
) -> ResearchSufficiency:
    deps = SufficiencyAssessorDeps(
        finding_ids={finding.id for finding in findings},
        conflict_ids={conflict.id for conflict in conflicts},
    )

    prompt = build_sufficiency_prompt(
        question=question,
        findings=findings,
        conflicts=conflicts,
    )

    return await run_model_call(
        assessor,
        label="SUFFICIENCY",
        prompt=prompt,
        signals=signals,
        deps=deps,
    )
