import os
from functools import wraps

import httpx
from dotenv import load_dotenv
from pydantic_ai import Agent, Tool, UsageLimits
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from tsuzuri.tools.vndb import get_vndb, search_vndb
from tsuzuri.tools.web import (
    WebPageResponse,
    read_webpage,
    web_search,
)

load_dotenv()


@wraps(read_webpage)
def read_webpage_for_agent(url: str) -> WebPageResponse:
    """Adapt webpage retrieval failures into agent-readable tool failures."""
    try:
        return read_webpage(url)

    except httpx.HTTPStatusError as error:
        status = error.response.status_code

        raise ToolFailed(
            f"Unable to read webpage {url}: HTTP {status}. "
            "Try another source instead."
        ) from error

    except RuntimeError as error:
        raise ToolFailed(
            f"Unable to extract readable content from {url}. "
            "Try another source or use other available evidence."
        ) from error


model = OpenAIChatModel(
    os.environ["LLM_MODEL"],
    provider=OpenAIProvider(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
    ),
)

search_vndb_tool = Tool(
    search_vndb,
    description=(
        "Search VNDB by visual novel title and return candidate works "
        "with their VNDB IDs. Use this when the exact VNDB ID is unknown."
    ),
)

get_vndb_tool = Tool(
    get_vndb,
    description=(
        "Get detailed information about one exact VNDB visual novel. "
        "Use this when the exact VNDB ID is already known from user input "
        "or from a previous search result."
    ),
)

web_search_tool = Tool(
    web_search,
    description=(
        "Search the public web for current or external information. "
        "Returns candidate pages with title, URL, snippet, and relevance score. "
        "Use this to discover sources when structured tools are insufficient."
    ),
)

read_webpage_tool = Tool(
    read_webpage_for_agent,
    name="read_webpage",
    description=(
        "Read and extract the main text from a specific webpage URL. "
        "Use this when an important factual claim should be checked "
        "against an original source page."
    ),
)

agent = Agent(
    model,
    tools=[
        search_vndb_tool,
        get_vndb_tool,
        web_search_tool,
        read_webpage_tool,
    ],
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

result = agent.run_sync(
    (
        "请确认原版 STEINS;GATE 是什么时候登陆 Steam 的。"
        "必须基于工具查询结果回答，不要把未经工具验证的模型背景知识"
        "当作已确认事实。"
    ),
    usage_limits=UsageLimits(
        request_limit=8,
        tool_calls_limit=6,
    ),
)

print("=== OUTPUT ===")
print(result.output)

print("\n=== USAGE ===")
print(result.usage)

print("\n=== MESSAGES ===")
for message in result.all_messages():
    print(message)
