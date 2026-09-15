import asyncio
import os
import time
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum, StrEnum
from pathlib import Path
from threading import Lock
from typing import Any

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from pydantic_ai import (
    Agent,
    FunctionToolResultEvent,
    ModelRetry,
    ModelSettings,
    PartEndEvent,
    RunContext,
    Tool,
    UsageLimits,
)
from pydantic_ai.exceptions import (
    ToolFailed,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage

from tsuzuri.tools.vndb import (
    VndbSearchResponse,
    VndbVisualNovelDetail,
    get_vndb,
    search_vndb,
)
from tsuzuri.tools.web import (
    WebPageResponse,
    WebSearchResponse,
    read_webpage,
    web_search,
)

load_dotenv()


class SourceKind(StrEnum):
    STRUCTURED_API = "structured_api"
    WEB_SEARCH_SNIPPET = "web_search_snippet"
    WEB_PAGE = "web_page"


class ResearchMode(StrEnum):
    FAST = "fast"
    GROUNDED = "grounded"
    DEEP = "deep"


@dataclass
class SourceCandidate:
    id: str
    kind: SourceKind
    observation_id: str

    source_name: str
    source_url: str | None = None
    source_id: str | None = None

    content: object | None = None


class ObservationStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class Observation:
    id: str
    tool_name: str
    arguments: dict[str, object]
    status: ObservationStatus
    result: object | None = None
    error: str | None = None


class ExtractedEvidence(BaseModel):
    claim: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "A concise factual claim directly supported by the provided source."
        ),
    )

    support: str = Field(
        min_length=1,
        max_length=800,
        description=(
            "A short exact excerpt copied from the provided source content."
        ),
    )


class EvidenceExtraction(BaseModel):
    evidence: list[ExtractedEvidence] = Field(default_factory=list)


class RelevantSourceSelection(BaseModel):
    source_candidate_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Source candidate IDs whose contents may directly "
            "support a useful answer to the research question."
        ),
    )


class EvidenceGroup(BaseModel):
    representative_evidence_id: str = Field(
        description=(
            "One evidence ID whose existing claim best represents this group."
        ),
    )

    evidence_ids: list[str] = Field(
        min_length=1,
        description=(
            "Evidence records that support substantially the same "
            "factual proposition."
        ),
    )


class EvidenceConflict(BaseModel):
    left_group_index: int
    right_group_index: int


class EvidenceConsolidation(BaseModel):
    groups: list[EvidenceGroup] = Field(
        default_factory=list,
    )

    conflicts: list[EvidenceConflict] = Field(
        default_factory=list,
    )


@dataclass
class Finding:
    id: str
    claim: str
    evidence_ids: list[str]


class ResearchSufficiencyStatus(StrEnum):
    SUFFICIENT = "sufficient"
    SUFFICIENT_WITH_CONFLICT = "sufficient_with_conflict"
    NEEDS_MORE_RESEARCH = "needs_more_research"


class ResearchGapKind(StrEnum):
    MISSING_COVERAGE = "missing_coverage"
    UNRESOLVED_CONFLICT = "unresolved_conflict"


class ResearchGap(BaseModel):
    kind: ResearchGapKind

    description: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "The specific information gap that prevents "
            "the research question from being answered responsibly."
        ),
    )

    related_finding_ids: list[str] = Field(
        default_factory=list,
    )

    related_conflict_ids: list[str] = Field(
        default_factory=list,
    )


class ResearchSufficiency(BaseModel):
    status: ResearchSufficiencyStatus

    supporting_finding_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Existing findings that materially help answer "
            "the research question."
        ),
    )

    unresolved_conflict_ids: list[str] = Field(
        default_factory=list,
    )

    gaps: list[ResearchGap] = Field(
        default_factory=list,
    )


class GroundedStatement(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=800,
    )

    evidence_ids: list[str] = Field(
        min_length=1,
    )


class FinalSynthesis(BaseModel):
    statements: list[GroundedStatement] = Field(
        min_length=1,
        max_length=6,
    )

    limitations: list[str] = Field(
        default_factory=list,
        max_length=4,
    )


@dataclass
class FindingConflict:
    id: str
    left_finding_id: str
    right_finding_id: str


@dataclass
class ResearchRoundResult:
    result: Any | None
    usage: RunUsage
    stop_reason: str


@dataclass
class PipelineWarning:
    stage: str
    error_type: str
    message: str


@dataclass
class PipelineMetrics:
    model_requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    elapsed_seconds: float = 0.0
    wall_seconds: float = 0.0

    def add_usage(self, usage: RunUsage) -> None:
        self.model_requests += usage.requests or 0
        self.tool_calls += usage.tool_calls or 0
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0

        if usage.cost is not None:
            self.cost += float(usage.cost)


@dataclass
class Evidence:
    id: str
    source_candidate_id: str
    observation_id: str
    claim: str
    support: str


@dataclass
class ResearchState:
    observations: list[Observation] = field(default_factory=list)
    sources: list[SourceCandidate] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    processed_source_ids: set[str] = field(default_factory=set)

    _next_observation_id: int = field(
        default=1,
        init=False,
        repr=False,
    )

    _next_source_id: int = field(
        default=1,
        init=False,
        repr=False,
    )

    _next_evidence_id: int = field(
        default=1,
        init=False,
        repr=False,
    )

    _lock: Lock = field(
        default_factory=Lock,
        init=False,
        repr=False,
    )

    def record_observation(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        status: ObservationStatus,
        result: object | None = None,
        error: str | None = None,
    ) -> Observation:
        with self._lock:
            observation = Observation(
                id=f"obs_{self._next_observation_id}",
                tool_name=tool_name,
                arguments=arguments,
                status=status,
                result=result,
                error=error,
            )

            self._next_observation_id += 1
            self.observations.append(observation)

        return observation

    def record_source(
        self,
        *,
        kind: SourceKind,
        observation_id: str,
        source_name: str,
        source_url: str | None = None,
        source_id: str | None = None,
        content: object | None = None,
    ) -> SourceCandidate:
        with self._lock:
            source = SourceCandidate(
                id=f"src_{self._next_source_id}",
                kind=kind,
                observation_id=observation_id,
                source_name=source_name,
                source_url=source_url,
                source_id=source_id,
                content=content,
            )

            self._next_source_id += 1
            self.sources.append(source)

        return source

    def record_evidence(
        self,
        *,
        source: SourceCandidate,
        claim: str,
        support: str,
    ) -> Evidence:
        with self._lock:
            evidence = Evidence(
                id=f"ev_{self._next_evidence_id}",
                source_candidate_id=source.id,
                observation_id=source.observation_id,
                claim=claim,
                support=support,
            )

            self._next_evidence_id += 1
            self.evidence.append(evidence)

        return evidence


@dataclass
class SourceSelectorDeps:
    source_ids: set[str]


@dataclass
class EvidenceExtractorDeps:
    source_text: str


@dataclass
class EvidenceConsolidatorDeps:
    evidence_ids: set[str]


@dataclass
class SufficiencyAssessorDeps:
    finding_ids: set[str]
    conflict_ids: set[str]


@dataclass
class GroundedSynthesisDeps:
    evidence_ids: set[str]


def source_content_to_text(content: object | None) -> str:
    if content is None:
        return ""

    if isinstance(content, BaseModel):
        return content.model_dump_json(indent=2)

    if isinstance(content, str):
        return content

    return repr(content)


def chunks[T](items: list[T], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def web_search_for_agent(
    ctx: RunContext[ResearchState],
    query: str,
    max_results: int = 5,
) -> WebSearchResponse:
    arguments = {
        "query": query,
        "max_results": max_results,
    }

    try:
        result = web_search(
            query=query,
            max_results=max_results,
        )

    except httpx.HTTPError as error:
        ctx.deps.record_observation(
            tool_name="web_search",
            arguments=arguments,
            status=ObservationStatus.FAILED,
            error=str(error),
        )

        raise ToolFailed(
            f"Web search failed: {error}. "
            "Try another source or research approach."
        ) from error

    except RuntimeError as error:
        ctx.deps.record_observation(
            tool_name="web_search",
            arguments=arguments,
            status=ObservationStatus.FAILED,
            error=str(error),
        )

        raise ToolFailed(str(error)) from error

    observation = ctx.deps.record_observation(
        tool_name="web_search",
        arguments=arguments,
        status=ObservationStatus.SUCCESS,
        result=result,
    )

    for item in result.results:
        ctx.deps.record_source(
            kind=SourceKind.WEB_SEARCH_SNIPPET,
            observation_id=observation.id,
            source_name=item.title,
            source_url=item.url,
            content=item.snippet,
        )

    return result


def search_vndb_for_agent(
    ctx: RunContext[ResearchState],
    query: str,
    results: int = 5,
) -> VndbSearchResponse:
    arguments = {
        "query": query,
        "results": results,
    }

    try:
        result = search_vndb(
            query=query,
            results=results,
        )

    except httpx.HTTPError as error:
        ctx.deps.record_observation(
            tool_name="search_vndb",
            arguments=arguments,
            status=ObservationStatus.FAILED,
            error=str(error),
        )

        raise ToolFailed(
            f"VNDB search failed: {error}. "
            "Use another source or try a different research approach."
        ) from error

    observation = ctx.deps.record_observation(
        tool_name="search_vndb",
        arguments=arguments,
        status=ObservationStatus.SUCCESS,
        result=result,
    )

    for item in result.results:
        ctx.deps.record_source(
            kind=SourceKind.STRUCTURED_API,
            observation_id=observation.id,
            source_name=item.title,
            source_id=item.id,
            source_url=f"https://vndb.org/{item.id}",
            content=item,
        )

    return result


def get_vndb_for_agent(
    ctx: RunContext[ResearchState],
    vn_id: str,
) -> VndbVisualNovelDetail | None:
    arguments = {
        "vn_id": vn_id,
    }

    try:
        result = get_vndb(vn_id)

    except httpx.HTTPError as error:
        ctx.deps.record_observation(
            tool_name="get_vndb",
            arguments=arguments,
            status=ObservationStatus.FAILED,
            error=str(error),
        )

        raise ToolFailed(
            f"Unable to retrieve VNDB entry {vn_id}: {error}. "
            "Use another source or verify the VNDB ID."
        ) from error

    observation = ctx.deps.record_observation(
        tool_name="get_vndb",
        arguments=arguments,
        status=ObservationStatus.SUCCESS,
        result=result,
    )

    if result is not None:
        ctx.deps.record_source(
            kind=SourceKind.STRUCTURED_API,
            observation_id=observation.id,
            source_name=result.title,
            source_id=result.id,
            source_url=f"https://vndb.org/{result.id}",
            content=result,
        )

    return result


def read_webpage_for_agent(
    ctx: RunContext[ResearchState],
    url: str,
) -> WebPageResponse:
    arguments = {
        "url": url,
    }

    try:
        result = read_webpage(url)

    except httpx.HTTPStatusError as error:
        status_code = error.response.status_code

        ctx.deps.record_observation(
            tool_name="read_webpage",
            arguments=arguments,
            status=ObservationStatus.FAILED,
            error=f"HTTP {status_code}",
        )

        raise ToolFailed(
            f"Unable to read webpage {url}: HTTP {status_code}. "
            "Try another source instead."
        ) from error

    except httpx.HTTPError as error:
        ctx.deps.record_observation(
            tool_name="read_webpage",
            arguments=arguments,
            status=ObservationStatus.FAILED,
            error=str(error),
        )

        raise ToolFailed(
            f"Unable to read webpage {url}: {error}. "
            "Try another source instead."
        ) from error

    except RuntimeError as error:
        ctx.deps.record_observation(
            tool_name="read_webpage",
            arguments=arguments,
            status=ObservationStatus.FAILED,
            error=str(error),
        )

        raise ToolFailed(
            f"Unable to extract readable content from {url}. "
            "Try another source or use other available evidence."
        ) from error

    observation = ctx.deps.record_observation(
        tool_name="read_webpage",
        arguments=arguments,
        status=ObservationStatus.SUCCESS,
        result=result,
    )

    ctx.deps.record_source(
        kind=SourceKind.WEB_PAGE,
        observation_id=observation.id,
        source_name=result.url,
        source_url=result.url,
        content=result.content,
    )

    return result


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


LIVE_LOG_PATH = Path(__file__).resolve().parent / "research_live.log"

pipeline_warnings: list[PipelineWarning] = []

pipeline_metrics = PipelineMetrics()

final_capability: str | None = None


def log_progress(message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"

    print(line, flush=True)

    with LIVE_LOG_PATH.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(line + "\n")
        file.flush()


def record_pipeline_warning(
    stage: str,
    error: BaseException,
) -> None:
    pipeline_warnings.append(
        PipelineWarning(
            stage=stage,
            error_type=type(error).__name__,
            message=str(error),
        )
    )


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
    output_path = Path(__file__).resolve().parent / "research_run.md"

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


openai_client = AsyncOpenAI(
    base_url=os.environ["LLM_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
    max_retries=0,
)

model = OpenAIChatModel(
    os.environ["LLM_MODEL"],
    provider=OpenAIProvider(
        openai_client=openai_client,
    ),
    settings=ModelSettings(
        timeout=60,
    ),
)

search_vndb_tool = Tool(
    search_vndb_for_agent,
    name="search_vndb",
    description=(
        "Search VNDB by visual novel title and return candidate works "
        "with their VNDB IDs. Use this when the exact VNDB ID is unknown."
    ),
    takes_ctx=True,
)

get_vndb_tool = Tool(
    get_vndb_for_agent,
    name="get_vndb",
    description=(
        "Get detailed information about one exact VNDB visual novel. "
        "Use this when the exact VNDB ID is already known."
    ),
    takes_ctx=True,
)

web_search_tool = Tool(
    web_search_for_agent,
    name="web_search",
    description=(
        "Search the public web for current or external information. "
        "Use this to discover candidate sources."
    ),
    takes_ctx=True,
)

read_webpage_tool = Tool(
    read_webpage_for_agent,
    name="read_webpage",
    description=(
        "Read and extract the main content from a webpage URL. "
        "Use this to verify important claims against a source page."
    ),
    takes_ctx=True,
)

agent = Agent(
    model,
    tools=[
        search_vndb_tool,
        get_vndb_tool,
        web_search_tool,
        read_webpage_tool,
    ],
    deps_type=ResearchState,
    instructions="""
You are Tsuzuri, a personal ACGN research agent.

Use available tools for factual claims instead of relying on
unverified background knowledge.

Prefer structured sources when they directly answer the question.

For web research:
- web search is primarily for discovering candidate sources;
- when practical, verify important factual claims against the
  original source page;
- a search snippet may still be useful evidence, but do not claim
  that you read the original page unless read_webpage succeeded.

Prefer primary sources over secondary sources when both directly
support the claim.

If sources conflict or a page cannot be read, say so rather than
inventing missing information.
""".strip(),
)


@agent.on_event(PartEndEvent)
async def log_model_tool_request(
    ctx: RunContext[ResearchState],
    event: PartEndEvent,
) -> None:
    part = event.part

    if not isinstance(part, ToolCallPart):
        return

    log_progress(
        "MODEL -> TOOL "
        f"{part.tool_name} "
        f"args={part.args} "
        f"[requests={ctx.usage.requests}, "
        f"tools={ctx.usage.tool_calls}]"
    )


@agent.on_event(FunctionToolResultEvent)
async def log_tool_result(
    ctx: RunContext[ResearchState],
    event: FunctionToolResultEvent,
) -> None:
    part = event.part

    tool_name = getattr(
        part,
        "tool_name",
        "unknown",
    )

    outcome = getattr(
        part,
        "outcome",
        "success",
    )

    log_progress(
        "TOOL -> MODEL "
        f"{tool_name} "
        f"outcome={outcome} "
        f"[requests={ctx.usage.requests}, "
        f"tools={ctx.usage.tool_calls}]"
    )


evidence_extractor = Agent(
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


def normalize_text(text: str) -> str:
    return " ".join(text.split())


@evidence_extractor.output_validator
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


async def extract_evidence_from_source(
    *,
    question: str,
    source: SourceCandidate,
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

    log_progress(f"EXTRACTOR {source.id} sending")

    started = time.monotonic()

    usage = RunUsage()

    try:
        result = await evidence_extractor.run(
            prompt,
            deps=deps,
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=2,
            ),
        )

        pipeline_metrics.add_usage(usage)
    finally:
        elapsed = time.monotonic() - started

        pipeline_metrics.elapsed_seconds += elapsed

        log_progress(f"EXTRACTOR {source.id} finished elapsed={elapsed:.1f}s")

    return result.output


source_selector = Agent(
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


@source_selector.output_validator
def validate_selection(
    ctx: RunContext[SourceSelectorDeps],
    output: RelevantSourceSelection,
) -> RelevantSourceSelection:
    unknown = set(output.source_candidate_ids) - ctx.deps.source_ids

    if unknown:
        raise ModelRetry(f"Unknown source IDs: {sorted(unknown)}")

    return output


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
    *,
    question: str,
    sources: list[SourceCandidate],
) -> list[SourceCandidate]:
    deps = SourceSelectorDeps(
        source_ids={source.id for source in sources},
    )

    prompt = build_source_selection_prompt(
        question=question,
        sources=sources,
    )

    log_progress(f"SELECTOR sending sources={len(sources)}")

    started = time.monotonic()

    usage = RunUsage()

    try:
        result = await source_selector.run(
            prompt,
            deps=deps,
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=2,
            ),
        )

        pipeline_metrics.add_usage(usage)

        selected_ids = set(result.output.source_candidate_ids)

        return [source for source in sources if source.id in selected_ids]

    except Exception as error:
        log_progress(
            "SELECTOR degraded: "
            f"{type(error).__name__}: {error}; "
            "falling back to all sources"
        )

        record_pipeline_warning("selector", error)

        return sources

    finally:
        elapsed = time.monotonic() - started

        pipeline_metrics.elapsed_seconds += elapsed

        log_progress(f"SELECTOR finished elapsed={elapsed:.1f}s")


EXTRACTION_CONCURRENCY = 3

extraction_semaphore = asyncio.Semaphore(EXTRACTION_CONCURRENCY)


async def extract_evidence_with_limit(
    *,
    question: str,
    source: SourceCandidate,
) -> EvidenceExtraction:
    async with extraction_semaphore:
        return await extract_evidence_from_source(
            question=question,
            source=source,
        )


async def extract_evidence_for_pending_sources(
    *,
    question: str,
    research_state: ResearchState,
) -> int:
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
            question=question,
            sources=source_batch,
        )

        log_progress(
            "SOURCE SELECTION "
            f"selected={len(selected_sources)} "
            f"of={len(source_batch)}"
        )

        results = await asyncio.gather(
            *[
                extract_evidence_with_limit(
                    question=question,
                    source=source,
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

                record_pipeline_warning(
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


evidence_consolidator = Agent(
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


@evidence_consolidator.output_validator
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
    *,
    question: str,
    evidence: list[Evidence],
) -> EvidenceConsolidation:
    deps = EvidenceConsolidatorDeps(
        evidence_ids={item.id for item in evidence},
    )

    prompt = build_evidence_consolidation_prompt(
        question=question,
        evidence=evidence,
    )

    log_progress("CONSOLIDATOR model request sending")

    started = time.monotonic()

    usage = RunUsage()

    try:
        result = await evidence_consolidator.run(
            prompt,
            deps=deps,
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=2,
            ),
        )

        pipeline_metrics.add_usage(usage)

        return result.output
    finally:
        elapsed = time.monotonic() - started

        pipeline_metrics.elapsed_seconds += elapsed

        log_progress(
            f"CONSOLIDATOR model request finished elapsed={elapsed:.1f}s"
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


sufficiency_assessor = Agent(
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


@sufficiency_assessor.output_validator
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
    *,
    question: str,
    findings: list[Finding],
    conflicts: list[FindingConflict],
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

    log_progress("SUFFICIENCY model request sending")

    started = time.monotonic()

    usage = RunUsage()

    try:
        result = await sufficiency_assessor.run(
            prompt,
            deps=deps,
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=2,
            ),
        )

        pipeline_metrics.add_usage(usage)

        return result.output

    finally:
        elapsed = time.monotonic() - started

        pipeline_metrics.elapsed_seconds += elapsed

        log_progress(
            f"SUFFICIENCY model request finished elapsed={elapsed:.1f}s"
        )


grounded_synthesizer = Agent(
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


@grounded_synthesizer.output_validator
def validate_grounded_synthesis(
    ctx: RunContext[GroundedSynthesisDeps],
    output: FinalSynthesis,
) -> FinalSynthesis:
    for statement in output.statements:
        unknown = set(statement.evidence_ids) - ctx.deps.evidence_ids

        if unknown:
            raise ModelRetry(f"Unknown evidence IDs: {sorted(unknown)}")

    return output


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
    *,
    question: str,
    evidence: list[Evidence],
    sources: list[SourceCandidate],
) -> FinalSynthesis:
    deps = GroundedSynthesisDeps(
        evidence_ids={item.id for item in evidence},
    )

    prompt = build_grounded_synthesis_prompt(
        question=question,
        evidence=evidence,
        sources=sources,
    )

    log_progress("SYNTHESIZER model request sending")

    started = time.monotonic()

    usage = RunUsage()

    try:
        result = await grounded_synthesizer.run(
            prompt,
            deps=deps,
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=2,
            ),
        )

        pipeline_metrics.add_usage(usage)

        return result.output
    finally:
        elapsed = time.monotonic() - started

        pipeline_metrics.elapsed_seconds += elapsed

        log_progress(
            f"SYNTHESIZER model request finished elapsed={elapsed:.1f}s"
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


deep_synthesizer = Agent(
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


@deep_synthesizer.output_validator
def validate_deep_synthesis(
    ctx: RunContext[GroundedSynthesisDeps],
    output: FinalSynthesis,
) -> FinalSynthesis:
    for statement in output.statements:
        unknown = set(statement.evidence_ids) - ctx.deps.evidence_ids

        if unknown:
            raise ModelRetry(f"Unknown evidence IDs: {sorted(unknown)}")

    return output


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
    *,
    question: str,
    evidence: list[Evidence],
    sources: list[SourceCandidate],
    findings: list[Finding],
    conflicts: list[FindingConflict],
    sufficiency: ResearchSufficiency,
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

    log_progress("DEEP SYNTHESIZER model request sending")

    started = time.monotonic()

    usage = RunUsage()

    try:
        result = await deep_synthesizer.run(
            prompt,
            deps=deps,
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=2,
            ),
        )

        pipeline_metrics.add_usage(usage)

        return result.output
    finally:
        elapsed = time.monotonic() - started

        pipeline_metrics.elapsed_seconds += elapsed

        log_progress(
            f"DEEP SYNTHESIZER model request finished elapsed={elapsed:.1f}s"
        )


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
    findings: list[Finding] | None = None,
    conflicts: list[FindingConflict] | None = None,
    sufficiency: ResearchSufficiency | None = None,
) -> str:
    """Render the final answer with a graceful fallback ladder.

    deep synthesis (full research state) -> grounded synthesis
    -> deterministic evidence rendering.
    """
    global final_capability

    if (
        findings is not None
        and conflicts is not None
        and sufficiency is not None
    ):
        try:
            synthesis = await synthesize_deep_answer(
                question=question,
                evidence=evidence,
                sources=sources,
                findings=findings,
                conflicts=conflicts,
                sufficiency=sufficiency,
            )

            log_progress(
                "DEEP SYNTHESIS completed "
                f"statements={len(synthesis.statements)}"
            )

            final_capability = "deep"

            return render_final_answer(synthesis)
        except Exception as error:
            log_progress(
                "DEEP SYNTHESIS degraded: "
                f"{type(error).__name__}: {error}; "
                "falling back to grounded synthesis"
            )

            record_pipeline_warning("deep_synthesis", error)

    try:
        synthesis = await synthesize_grounded_answer(
            question=question,
            evidence=evidence,
            sources=sources,
        )

        log_progress(
            "GROUNDED SYNTHESIS completed "
            f"statements={len(synthesis.statements)}"
        )

        final_capability = "grounded"

        return render_final_answer(synthesis)
    except Exception as error:
        log_progress(
            "GROUNDED SYNTHESIS degraded: "
            f"{type(error).__name__}: {error}; "
            "using deterministic evidence rendering"
        )

        record_pipeline_warning("grounded_synthesis", error)

        final_capability = "deterministic"

        return render_evidence_fallback(evidence)


fast_synthesizer = Agent(
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
    *,
    question: str,
    sources: list[SourceCandidate],
) -> str:
    prompt = build_fast_synthesis_prompt(
        question=question,
        sources=sources,
    )

    log_progress("FAST SYNTHESIZER model request sending")

    started = time.monotonic()

    usage = RunUsage()

    try:
        result = await fast_synthesizer.run(
            prompt,
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=2,
            ),
        )

        pipeline_metrics.add_usage(usage)

        return result.output
    finally:
        elapsed = time.monotonic() - started

        pipeline_metrics.elapsed_seconds += elapsed

        log_progress(
            f"FAST SYNTHESIZER model request finished elapsed={elapsed:.1f}s"
        )


def build_gap_research_prompt(
    *,
    question: str,
    gap: ResearchGap,
    findings: list[Finding],
    conflicts: list[FindingConflict],
) -> str:
    related_findings = [
        finding for finding in findings if finding.id in gap.related_finding_ids
    ]

    related_conflicts = [
        conflict
        for conflict in conflicts
        if conflict.id in gap.related_conflict_ids
    ]

    finding_text = "\n".join(
        f"- {finding.id}: {finding.claim}" for finding in related_findings
    )

    conflict_text = "\n".join(
        (
            f"- {conflict.id}: "
            f"{conflict.left_finding_id} ↔ "
            f"{conflict.right_finding_id}"
        )
        for conflict in related_conflicts
    )

    return f"""
You are continuing an existing research task.

ORIGINAL QUESTION:
{question}

SPECIFIC RESEARCH GAP:
{gap.description}

RELATED FINDINGS:
{finding_text or "(none)"}

RELATED CONFLICTS:
{conflict_text or "(none)"}

Research only this specific gap.

Rules:
- Do not restart broad research from scratch.
- Prefer a direct or primary source when the gap identifies one.
- Use only the tools necessary to investigate this gap.
- Do not attempt to write the final answer.
- Do not invent a resolution if the new sources remain conflicting.
- Stop once you have gathered useful information for this gap.

The runtime will capture any retrieved sources automatically.
""".strip()


question = (
    "请确认原版 STEINS;GATE 是什么时候登陆 Steam 的。"
    "如果有必要请尝试交叉核对 SteamDB。"
    "必须基于工具结果回答。"
)

mode = ResearchMode.GROUNDED

MAX_RESEARCH_ROUNDS = 1

EVIDENCE_BATCH_SIZE = 5

INITIAL_REQUEST_LIMIT = 4
INITIAL_TOOL_CALLS_LIMIT = 4

GAP_REQUEST_LIMIT = 3
GAP_TOOL_CALLS_LIMIT = 3

FAST_REQUEST_LIMIT = 3
FAST_TOOL_CALLS_LIMIT = 2


async def run_research_round(
    *,
    prompt: str,
    research_state: ResearchState,
    request_limit: int,
    tool_calls_limit: int,
) -> ResearchRoundResult:
    usage = RunUsage()

    started = time.monotonic()

    try:
        result = await agent.run(
            prompt,
            deps=research_state,
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=request_limit,
                tool_calls_limit=tool_calls_limit,
            ),
        )

        return ResearchRoundResult(
            result=result,
            usage=usage,
            stop_reason="model_finished",
        )

    except UsageLimitExceeded as error:
        log_progress(f"RESEARCH ROUND stopped by budget: {error}")

        return ResearchRoundResult(
            result=None,
            usage=usage,
            stop_reason="budget_exhausted",
        )

    finally:
        pipeline_metrics.add_usage(usage)

        pipeline_metrics.elapsed_seconds += time.monotonic() - started


async def main() -> None:
    global final_capability

    wall_started = time.monotonic()

    research_state = ResearchState()

    LIVE_LOG_PATH.write_text("", encoding="utf-8")

    pipeline_warnings.clear()

    pipeline_metrics.model_requests = 0
    pipeline_metrics.tool_calls = 0
    pipeline_metrics.input_tokens = 0
    pipeline_metrics.output_tokens = 0
    pipeline_metrics.cost = 0.0
    pipeline_metrics.elapsed_seconds = 0.0

    final_capability = None

    main_usage = RunUsage()

    result = None
    final_answer: str | None = None
    consolidation = None
    findings = None
    finding_conflicts = None
    sufficiency = None
    stop_reason: str | None = None
    pipeline_stop: str | None = None
    run_error: Exception | None = None
    stage = "main_agent"

    try:
        if mode is ResearchMode.FAST:
            log_progress("FAST RESEARCH started")

            round_result = await run_research_round(
                prompt=question,
                research_state=research_state,
                request_limit=FAST_REQUEST_LIMIT,
                tool_calls_limit=FAST_TOOL_CALLS_LIMIT,
            )

            main_usage = round_result.usage
            result = round_result.result
            stop_reason = round_result.stop_reason

            if result is not None:
                final_answer = str(result.output)
                final_capability = "agent"
            elif research_state.sources:
                stage = "fast_synthesis"

                log_progress(
                    "FAST RESEARCH budget exhausted; "
                    "finalizing from captured sources"
                )

                final_answer = await synthesize_fast_answer(
                    question=question,
                    sources=research_state.sources,
                )

                final_capability = "fast_synthesis"
            else:
                raise RuntimeError(
                    "Fast research produced no sources and no answer."
                )

            log_progress(
                f"FAST RESEARCH stopped reason={round_result.stop_reason}"
            )

            pipeline_stop = "completed"

        elif mode is ResearchMode.GROUNDED:
            log_progress("GROUNDED RESEARCH started")

            round_result = await run_research_round(
                prompt=question,
                research_state=research_state,
                request_limit=INITIAL_REQUEST_LIMIT,
                tool_calls_limit=INITIAL_TOOL_CALLS_LIMIT,
            )

            main_usage = round_result.usage
            result = round_result.result
            stop_reason = round_result.stop_reason

            log_progress(
                "GROUNDED RESEARCH stopped "
                f"reason={round_result.stop_reason} "
                f"[requests={main_usage.requests}, "
                f"tools={main_usage.tool_calls}]"
            )

            stage = "evidence_extraction"

            log_progress("EVIDENCE EXTRACTION started")

            total_evidence = await extract_evidence_for_pending_sources(
                question=question,
                research_state=research_state,
            )

            log_progress(
                f"EVIDENCE EXTRACTION completed evidence={total_evidence}"
            )

            if not research_state.evidence:
                raise RuntimeError(
                    "Grounded research produced no validated evidence."
                )

            stage = "grounded_synthesis"

            log_progress("GROUNDED SYNTHESIS started")

            final_answer = await produce_final_answer(
                question=question,
                evidence=research_state.evidence,
                sources=research_state.sources,
            )

            pipeline_stop = "completed"

        else:
            log_progress("INITIAL RESEARCH started")

            round_result = await run_research_round(
                prompt=question,
                research_state=research_state,
                request_limit=INITIAL_REQUEST_LIMIT,
                tool_calls_limit=INITIAL_TOOL_CALLS_LIMIT,
            )

            main_usage = round_result.usage
            result = round_result.result
            stop_reason = round_result.stop_reason

            log_progress(
                "INITIAL RESEARCH stopped "
                f"reason={round_result.stop_reason} "
                f"[requests={main_usage.requests}, "
                f"tools={main_usage.tool_calls}]"
            )

            stage = "evidence_extraction"

            log_progress("EVIDENCE EXTRACTION started")

            total_evidence = await extract_evidence_for_pending_sources(
                question=question,
                research_state=research_state,
            )

            log_progress(
                f"EVIDENCE EXTRACTION completed evidence={total_evidence}"
            )

            if research_state.evidence:
                try:
                    stage = "evidence_consolidation"

                    log_progress("EVIDENCE CONSOLIDATION started")

                    consolidation = await consolidate_evidence(
                        question=question,
                        evidence=research_state.evidence,
                    )

                    findings = build_findings(
                        consolidation=consolidation,
                        evidence=research_state.evidence,
                    )

                    finding_conflicts = build_finding_conflicts(
                        consolidation=consolidation,
                        findings=findings,
                    )

                    log_progress(
                        "EVIDENCE CONSOLIDATION completed "
                        f"groups={len(consolidation.groups)} "
                        f"conflicts={len(consolidation.conflicts)}"
                    )
                except Exception as error:
                    log_progress(
                        "CONSOLIDATION degraded: "
                        f"{type(error).__name__}: {error}; "
                        "answering from evidence only"
                    )

                    record_pipeline_warning(
                        "evidence_consolidation",
                        error,
                    )
            else:
                log_progress("EVIDENCE CONSOLIDATION skipped (no evidence)")

            if findings is not None:
                try:
                    stage = "research_sufficiency"

                    log_progress("RESEARCH SUFFICIENCY started")

                    sufficiency = await assess_research_sufficiency(
                        question=question,
                        findings=findings,
                        conflicts=finding_conflicts,
                    )

                    log_progress(
                        "RESEARCH SUFFICIENCY completed "
                        f"status={sufficiency.status.value} "
                        f"gaps={len(sufficiency.gaps)}"
                    )
                except Exception as error:
                    log_progress(
                        "SUFFICIENCY degraded: "
                        f"{type(error).__name__}: {error}; "
                        "answering without sufficiency assessment"
                    )

                    record_pipeline_warning(
                        "research_sufficiency",
                        error,
                    )

            research_round = 0

            while (
                sufficiency is not None
                and sufficiency.status
                == ResearchSufficiencyStatus.NEEDS_MORE_RESEARCH
                and research_round < MAX_RESEARCH_ROUNDS
            ):
                research_round += 1

                if not sufficiency.gaps:
                    break

                gap = sufficiency.gaps[0]

                previous_consolidation = consolidation
                previous_findings = findings
                previous_conflicts = finding_conflicts
                previous_sufficiency = sufficiency

                try:
                    stage = "gap_research"

                    log_progress(
                        f"GAP RESEARCH round={research_round} kind={gap.kind.value}"
                    )

                    existing_source_ids = {
                        source.id for source in research_state.sources
                    }

                    gap_prompt = build_gap_research_prompt(
                        question=question,
                        gap=gap,
                        findings=findings,
                        conflicts=finding_conflicts,
                    )

                    gap_round = await run_research_round(
                        prompt=gap_prompt,
                        research_state=research_state,
                        request_limit=GAP_REQUEST_LIMIT,
                        tool_calls_limit=GAP_TOOL_CALLS_LIMIT,
                    )

                    log_progress(
                        f"GAP RESEARCH stopped reason={gap_round.stop_reason}"
                    )

                    new_sources = [
                        source
                        for source in research_state.sources
                        if source.id not in existing_source_ids
                    ]

                    log_progress(
                        f"GAP RESEARCH retrieved sources={len(new_sources)}"
                    )

                    if not new_sources:
                        log_progress("GAP RESEARCH stopped: no new sources")
                        break

                    stage = "gap_evidence_extraction"

                    new_evidence = await extract_evidence_for_pending_sources(
                        question=question,
                        research_state=research_state,
                    )

                    log_progress(f"GAP RESEARCH evidence={new_evidence}")

                    stage = "gap_consolidation"

                    consolidation = await consolidate_evidence(
                        question=question,
                        evidence=research_state.evidence,
                    )

                    findings = build_findings(
                        consolidation=consolidation,
                        evidence=research_state.evidence,
                    )

                    finding_conflicts = build_finding_conflicts(
                        consolidation=consolidation,
                        findings=findings,
                    )

                    stage = "research_sufficiency"

                    sufficiency = await assess_research_sufficiency(
                        question=question,
                        findings=findings,
                        conflicts=finding_conflicts,
                    )

                    log_progress(
                        f"GAP RESEARCH reassessed status={sufficiency.status.value}"
                    )
                except Exception as error:
                    consolidation = previous_consolidation
                    findings = previous_findings
                    finding_conflicts = previous_conflicts
                    sufficiency = previous_sufficiency

                    log_progress(
                        "GAP ENRICHMENT degraded; "
                        "restored previous validated state"
                    )

                    record_pipeline_warning(stage, error)

                    break

            if not research_state.evidence:
                raise RuntimeError(
                    "Deep research produced no validated evidence."
                )

            if findings is not None and sufficiency is not None:
                stage = "deep_synthesis"

                log_progress("DEEP SYNTHESIS started")
            else:
                stage = "grounded_synthesis"

                log_progress("DEEP SYNTHESIS degraded to grounded synthesis")

            final_answer = await produce_final_answer(
                question=question,
                evidence=research_state.evidence,
                sources=research_state.sources,
                findings=findings,
                conflicts=finding_conflicts,
                sufficiency=sufficiency,
            )

            if sufficiency is not None:
                pipeline_stop = sufficiency.status.value
            else:
                pipeline_stop = "degraded"

    except Exception as error:
        run_error = error

        log_progress(f"RUN FAILED in {stage}: {type(error).__name__}: {error}")

    finally:
        pipeline_metrics.wall_seconds = time.monotonic() - wall_started

        report_path = write_research_report(
            final_answer=final_answer,
            usage=main_usage,
            research_state=research_state,
            consolidation=consolidation,
            findings=findings,
            finding_conflicts=finding_conflicts,
            sufficiency=sufficiency,
            error=run_error,
            stage=stage,
            mode=mode.value,
            warnings=pipeline_warnings,
            final_capability=final_capability,
            metrics=pipeline_metrics,
            research_round_stop=stop_reason,
            pipeline_stop=pipeline_stop,
        )

        log_progress(f"REPORT written: {report_path}")

    if run_error is not None:
        raise run_error

    if final_answer is not None:
        print(final_answer)
    else:
        print("(research stopped; see report)")

    print()
    print(main_usage)
    print()
    print(f"Research report: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
