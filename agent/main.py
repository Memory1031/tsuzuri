import os

from dotenv import load_dotenv
from pydantic_ai import Agent, Tool, UsageLimits
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from tsuzuri.tools.vndb import get_vndb, search_vndb

load_dotenv()

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

agent = Agent(
    model,
    tools=[
        search_vndb_tool,
        get_vndb_tool,
    ],
    instructions="""
You are Tsuzuri, a personal ACGN research agent.

For factual information about visual novels, prefer information returned
by tools over background knowledge.

Do not invent factual details that are not supported by available tool results.
""".strip(),
)

result = agent.run_sync(
    ("请通过 VNDB 确认 STEINS;GATE 0 的 VNDB ID、发售日期和开发商。"),
    usage_limits=UsageLimits(
        request_limit=5,
        tool_calls_limit=1,
    ),
)

print("=== OUTPUT ===")
print(result.output)

print("\n=== USAGE ===")
print(result.usage)

print("\n=== MESSAGES ===")
for message in result.all_messages():
    print(message)
