"""Generic runtime mechanics shared by research components.

This module deliberately owns no research semantics: semantic
decisions live in agent instructions, research contracts live in the
component modules. It only provides model construction, run-level
signals, live logging and the per-call timing/metrics wrapper.
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai import Agent, ModelSettings, UsageLimits
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage

from tsuzuri.research.models import PipelineMetrics, PipelineWarning

# agent/tsuzuri/research/runtime.py -> agent/
AGENT_DIR = Path(__file__).resolve().parents[2]

LIVE_LOG_PATH = AGENT_DIR / "research_live.log"
REPORT_PATH = AGENT_DIR / "research_run.md"

ATTEMPT_TIMEOUT_SECONDS = 60


def build_model() -> OpenAIChatModel:
    """OpenAI-compatible model with provider retries disabled.

    Latency policy: one attempt bounded by ModelSettings(timeout);
    retry policy belongs to the runtime, not the provider SDK.
    """
    openai_client = AsyncOpenAI(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        max_retries=0,
    )

    return OpenAIChatModel(
        os.environ["LLM_MODEL"],
        provider=OpenAIProvider(openai_client=openai_client),
        settings=ModelSettings(timeout=ATTEMPT_TIMEOUT_SECONDS),
    )


@dataclass
class RunSignals:
    """Cross-cutting state for one research run."""

    warnings: list[PipelineWarning] = field(default_factory=list)
    metrics: PipelineMetrics = field(default_factory=PipelineMetrics)
    final_capability: str | None = None
    stage: str = "main_agent"

    def warn(self, stage: str, error: BaseException) -> None:
        self.warnings.append(
            PipelineWarning(
                stage=stage,
                error_type=type(error).__name__,
                message=str(error),
            )
        )


def log_progress(message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"

    print(line, flush=True)

    with LIVE_LOG_PATH.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(line + "\n")
        file.flush()


def source_content_to_text(content: object | None) -> str:
    if content is None:
        return ""

    if isinstance(content, BaseModel):
        return content.model_dump_json(indent=2)

    if isinstance(content, str):
        return content

    return repr(content)


async def run_model_call(
    agent: Agent[Any, Any],
    *,
    label: str,
    prompt: str,
    signals: RunSignals,
    deps: Any = None,
    request_limit: int = 2,
    sending_message: str | None = None,
    finished_prefix: str | None = None,
) -> Any:
    """Run one agent call with timing, usage metrics and live logging.

    Usage is only accumulated on success, matching the laboratory
    implementation; elapsed time is always accumulated.
    """
    log_progress(sending_message or f"{label} model request sending")

    started = time.monotonic()

    usage = RunUsage()

    try:
        result = await agent.run(
            prompt,
            deps=deps,
            usage=usage,
            usage_limits=UsageLimits(
                request_limit=request_limit,
            ),
        )

        signals.metrics.add_usage(usage)

        return result.output
    finally:
        elapsed = time.monotonic() - started

        signals.metrics.elapsed_seconds += elapsed

        log_progress(
            f"{finished_prefix or f'{label} model request'} "
            f"finished elapsed={elapsed:.1f}s"
        )
