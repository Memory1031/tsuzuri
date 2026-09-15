"""Source selection and single-source evidence extraction.

Contract (validated in P2.2):

- the selector sees a whole batch and only chooses source IDs;
- the extractor physically sees exactly one source per invocation,
  so cross-source semantic leakage is structurally impossible;
- the runtime mechanically verifies that every support quote exists
  in the source text and fills provenance the model never regenerates.
"""

import asyncio

from pydantic_ai import Agent, ModelRetry, RunContext

from tsuzuri.research.models import (
    EvidenceExtraction,
    EvidenceExtractorDeps,
    RelevantSourceSelection,
    SourceCandidate,
    SourceSelectorDeps,
)
from tsuzuri.research.runtime import (
    RunSignals,
    log_progress,
    run_model_call,
    source_content_to_text,
)
from tsuzuri.research.state import ResearchState

EVIDENCE_BATCH_SIZE = 5

EXTRACTION_CONCURRENCY = 3

extraction_semaphore = asyncio.Semaphore(EXTRACTION_CONCURRENCY)


def chunks[T](items: list[T], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def validate_evidence_support(
    ctx: RunContext[EvidenceExtractorDeps],
    output: EvidenceExtraction,
) -> EvidenceExtraction:
    normalized_source = normalize_text(ctx.deps.source_text)

    for item in output.evidence:
        normalized_support = normalize_text(item.support)

        if normalized_support not in normalized_source:
            raise ModelRetry(
                "Every support field must be copied "
                "directly from SOURCE_CONTENT. "
                f"The support for claim {item.claim!r} "
                "could not be found."
            )

    return output


def build_evidence_extractor(model) -> Agent[EvidenceExtractorDeps, EvidenceExtraction]:
    extractor: Agent[EvidenceExtractorDeps, EvidenceExtraction] = Agent(
        model,
        deps_type=EvidenceExtractorDeps,
        output_type=EvidenceExtraction,
        instructions="""
You are an evidence extraction component.

You receive one research question and exactly one source.

Extract only factual claims that:
1. directly help answer the research question;
2. are explicitly supported by that source.

Rules:
- The claim must be fully supported by this source alone.
- Do not make comparisons with, references to, or conclusions
  based on any other source.
- Do not infer causes, motivations, explanations, or missing facts.
- Do not reconcile conflicting sources.
- Do not use background knowledge.
- Every support field must be a short exact excerpt copied from
  the source content.
- Do not return multiple evidence records that express
  substantially the same factual proposition from this source.
- It is valid to return no evidence if the source is irrelevant
  or does not directly support a useful claim.
""",
    )

    extractor.output_validator(validate_evidence_support)

    return extractor


async def extract_evidence_from_source(
    extractor: Agent[EvidenceExtractorDeps, EvidenceExtraction],
    *,
    question: str,
    source: SourceCandidate,
    signals: RunSignals,
) -> EvidenceExtraction:
    source_text = source_content_to_text(source.content)

    deps = EvidenceExtractorDeps(
        source_text=source_text,
    )

    prompt = f"""
RESEARCH QUESTION:
{question}

SOURCE:
kind: {source.kind.value}
name: {source.source_name}
url: {source.source_url or "-"}
source_id: {source.source_id or "-"}

SOURCE_CONTENT:
<<<SOURCE
{source_text}
SOURCE
"""

    return await run_model_call(
        extractor,
        label=f"EXTRACTOR {source.id}",
        prompt=prompt,
        signals=signals,
        deps=deps,
        sending_message=f"EXTRACTOR {source.id} sending",
        finished_prefix=f"EXTRACTOR {source.id}",
    )


def validate_selection(
    ctx: RunContext[SourceSelectorDeps],
    output: RelevantSourceSelection,
) -> RelevantSourceSelection:
    unknown = set(output.source_candidate_ids) - ctx.deps.source_ids

    if unknown:
        raise ModelRetry(f"Unknown source IDs: {sorted(unknown)}")

    return output


def build_source_selector(
    model,
) -> Agent[SourceSelectorDeps, RelevantSourceSelection]:
    selector: Agent[SourceSelectorDeps, RelevantSourceSelection] = Agent(
        model,
        deps_type=SourceSelectorDeps,
        output_type=RelevantSourceSelection,
        instructions="""
You are a source relevance selector.

Given one research question and multiple source candidates,
select only sources whose own contents may directly provide
useful evidence for answering the question.

Rules:
- Return source IDs only.
- Do not extract claims.
- Do not combine information across sources.
- Do not answer the research question.
- Ignore clearly irrelevant search results.
- Use only source_candidate_id values supplied in SOURCES.
""".strip(),
    )

    selector.output_validator(validate_selection)

    return selector


def build_source_selection_prompt(
    *,
    question: str,
    sources: list[SourceCandidate],
) -> str:
    source_text_by_id = {
        source.id: source_content_to_text(source.content) for source in sources
    }

    source_blocks: list[str] = []

    for source in sources:
        source_blocks.append(
            f"""
<SOURCE id="{source.id}">
kind: {source.kind.value}
name: {source.source_name}
url: {source.source_url or "-"}
source_id: {source.source_id or "-"}

CONTENT:
{source_text_by_id[source.id]}
</SOURCE>
""".strip()
        )

    return f"""
RESEARCH QUESTION:
{question}

SOURCES:

{"\n\n".join(source_blocks)}
""".strip()


async def select_relevant_sources(
    selector: Agent[SourceSelectorDeps, RelevantSourceSelection],
    *,
    question: str,
    sources: list[SourceCandidate],
    signals: RunSignals,
) -> list[SourceCandidate]:
    """Select relevant sources; degrade to all sources on failure."""
    deps = SourceSelectorDeps(
        source_ids={source.id for source in sources},
    )

    prompt = build_source_selection_prompt(
        question=question,
        sources=sources,
    )

    try:
        result = await run_model_call(
            selector,
            label="SELECTOR",
            prompt=prompt,
            signals=signals,
            deps=deps,
            sending_message=f"SELECTOR sending sources={len(sources)}",
            finished_prefix="SELECTOR",
        )
    except Exception as error:
        log_progress(
            "SELECTOR degraded: "
            f"{type(error).__name__}: {error}; "
            "falling back to all sources"
        )

        signals.warn("selector", error)

        return sources

    selected_ids = set(result.source_candidate_ids)

    return [source for source in sources if source.id in selected_ids]


async def _extract_with_limit(
    extractor: Agent[EvidenceExtractorDeps, EvidenceExtraction],
    *,
    question: str,
    source: SourceCandidate,
    signals: RunSignals,
) -> EvidenceExtraction:
    async with extraction_semaphore:
        return await extract_evidence_from_source(
            extractor,
            question=question,
            source=source,
            signals=signals,
        )


async def extract_pending_evidence(
    *,
    selector: Agent[SourceSelectorDeps, RelevantSourceSelection],
    extractor: Agent[EvidenceExtractorDeps, EvidenceExtraction],
    question: str,
    research_state: ResearchState,
    signals: RunSignals,
) -> int:
    """Extract evidence for every unprocessed source; return new count.

    Failed single-source extractions are skipped (warn + keep the
    rest); all processed sources are marked so they are never
    re-extracted.
    """
    pending_sources = [
        source
        for source in research_state.sources
        if source.id not in research_state.processed_source_ids
    ]

    total_evidence = 0

    for source_batch in chunks(
        pending_sources,
        EVIDENCE_BATCH_SIZE,
    ):
        selected_sources = await select_relevant_sources(
            selector,
            question=question,
            sources=source_batch,
            signals=signals,
        )

        log_progress(
            "SOURCE SELECTION "
            f"selected={len(selected_sources)} "
            f"of={len(source_batch)}"
        )

        results = await asyncio.gather(
            *[
                _extract_with_limit(
                    extractor,
                    question=question,
                    source=source,
                    signals=signals,
                )
                for source in selected_sources
            ],
            return_exceptions=True,
        )

        for source, result in zip(
            selected_sources,
            results,
            strict=True,
        ):
            if isinstance(result, BaseException):
                log_progress(
                    f"EXTRACTOR {source.id} degraded: "
                    f"{type(result).__name__}: {result}"
                )

                signals.warn(
                    f"extractor_{source.id}",
                    result,
                )

                continue

            for item in result.evidence:
                research_state.record_evidence(
                    source=source,
                    claim=item.claim,
                    support=item.support,
                )

            total_evidence += len(result.evidence)

        research_state.processed_source_ids.update(
            source.id for source in source_batch
        )

    return total_evidence
