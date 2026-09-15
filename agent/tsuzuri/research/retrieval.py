"""Research agent, tool adapters and bounded research rounds.

Tool wrappers capture every retrieval attempt mechanically into
ResearchState (Observation + SourceCandidate); the model never needs
to remember to persist sources.
"""

import time
from typing import Any

import httpx
from pydantic_ai import (
    Agent,
    FunctionToolResultEvent,
    PartEndEvent,
    RunContext,
    Tool,
    UsageLimits,
)
from pydantic_ai.exceptions import ToolFailed, UsageLimitExceeded
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.usage import RunUsage

from tsuzuri.research.models import (
    Finding,
    FindingConflict,
    ObservationStatus,
    ResearchGap,
    ResearchRoundResult,
    SourceKind,
)
from tsuzuri.research.runtime import RunSignals, log_progress
from tsuzuri.research.state import ResearchState
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


RESEARCH_INSTRUCTIONS = """
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
""".strip()


def build_research_agent(model) -> Agent[ResearchState, Any]:
    """Research agent with state-capturing tools and live tool logs."""
    agent: Agent[ResearchState, Any] = Agent(
        model,
        tools=[
            Tool(
                search_vndb_for_agent,
                name="search_vndb",
                description=(
                    "Search VNDB by visual novel title and return candidate "
                    "works with their VNDB IDs. Use this when the exact VNDB "
                    "ID is unknown."
                ),
                takes_ctx=True,
            ),
            Tool(
                get_vndb_for_agent,
                name="get_vndb",
                description=(
                    "Get detailed information about one exact VNDB visual "
                    "novel. Use this when the exact VNDB ID is already known."
                ),
                takes_ctx=True,
            ),
            Tool(
                web_search_for_agent,
                name="web_search",
                description=(
                    "Search the public web for current or external "
                    "information. Use this to discover candidate sources."
                ),
                takes_ctx=True,
            ),
            Tool(
                read_webpage_for_agent,
                name="read_webpage",
                description=(
                    "Read and extract the main content from a webpage URL. "
                    "Use this to verify important claims against a source "
                    "page."
                ),
                takes_ctx=True,
            ),
        ],
        deps_type=ResearchState,
        instructions=RESEARCH_INSTRUCTIONS,
    )

    agent.on_event(PartEndEvent)(log_model_tool_request)
    agent.on_event(FunctionToolResultEvent)(log_tool_result)

    return agent


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


async def run_research_round(
    agent: Agent[ResearchState, Any],
    *,
    prompt: str,
    research_state: ResearchState,
    request_limit: int,
    tool_calls_limit: int,
    signals: RunSignals,
) -> ResearchRoundResult:
    """Run one bounded research round.

    Reaching the usage budget is a round boundary, not a run failure:
    everything captured so far stays in ResearchState.
    """
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
        signals.metrics.add_usage(usage)

        signals.metrics.elapsed_seconds += time.monotonic() - started


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
