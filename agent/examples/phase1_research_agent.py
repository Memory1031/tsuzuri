import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from tsuzuri.models.evidence import Evidence, EvidenceKind, EvidenceSource
from tsuzuri.tools.vndb import get_vndb, search_vndb
from tsuzuri.tools.web import read_webpage, web_search

load_dotenv()

MAX_STEPS = 5

ORIGINAL_QUESTION = (
    "请确认原版 STEINS;GATE 是什么时候登陆 Steam 的。"
    "必须基于工具查询结果回答，不要使用未经验证的模型背景知识。"
)

SYSTEM_PROMPT = """
You are an ACGN research agent.
For factual claims about works, releases, ratings, developers, and dates,
use information returned by tools as the source of truth.
Do not present information from your own background knowledge as verified fact.
If the available tool results do not contain enough information,
call another tool or explicitly state that the information is unverified.
Prefer primary sources over secondary sources when they directly support a claim.
Web search snippets may be used as evidence, but do not describe them as direct webpage verification.
If a primary source directly supports the requested fact and there is no strong conflicting evidence,
stop researching and answer.
If sources conflict, investigate the strongest conflicting sources, then report the disagreement
instead of searching indefinitely.
Avoid repeating equivalent searches that are unlikely to provide new evidence.

When you discover information that materially supports the user's question,
save it with save_evidence before it may leave the working context.
Choose source_ref only from refs explicitly provided in previous tool results.
Do not invent source refs.
Save only important evidence, not every available source.
Before ending the research phase, make sure all facts necessary to answer the user's
question have been saved as evidence.

The final answer will be generated from saved evidence only.
Information that is not saved as evidence will not be available during final synthesis.
""".strip()

FINAL_SYNTHESIS_PROMPT = """
You are producing the final answer for an ACGN research task.
The research phase has ended.

Answer the original user question using only the provided evidence.
Do not use unsupported factual information.
Treat support as source material and claim as the research agent's interpretation.
If evidence conflicts, explain the conflict.
If evidence is insufficient, explicitly say so.
""".strip()

evidence_store: list[Evidence] = []
evidence_sources: dict[str, EvidenceSource] = {}
tool_observations: dict[str, dict[str, Any]] = {}
observation_counter = 0


def register_web_search_sources(
    observation_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Expose search results as runtime-owned choices for save_evidence."""
    visible_results = []

    for index, item in enumerate(result.get("results", []), start=1):
        source_ref = f"{observation_id}:r{index}"

        evidence_sources[source_ref] = EvidenceSource(
            ref=source_ref,
            kind=EvidenceKind.WEB_SEARCH_SNIPPET,
            source_name=item["title"],
            source_url=item["url"],
            support=item["snippet"],
            observation_id=observation_id,
        )

        visible_results.append(
            {
                "ref": source_ref,
                **item,
            }
        )

    return {
        **result,
        "results": visible_results,
    }


def save_evidence(source_ref: str, claim: str) -> Evidence:
    """Let the model choose a source; let the runtime fill trusted metadata."""
    source = evidence_sources.get(source_ref)

    if source is None:
        raise ValueError(f"Unknown evidence source: {source_ref}")

    evidence = Evidence(
        id=f"ev_{len(evidence_store) + 1}",
        kind=source.kind,
        source_name=source.source_name,
        source_id=source.source_id,
        source_url=source.source_url,
        claim=claim,
        support=source.support,
        source_observation_id=source.observation_id,
    )

    evidence_store.append(evidence)
    return evidence


def build_evidence_context() -> str:
    """Project durable research state into the smaller final-answer context."""
    if not evidence_store:
        return "No evidence was successfully saved."

    return json.dumps(
        [
            evidence.model_dump(
                mode="json",
                exclude={"source_observation_id"},
            )
            for evidence in evidence_store
        ],
        ensure_ascii=False,
        indent=2,
    )


tool_registry = {
    "search_vndb": search_vndb,
    "get_vndb": get_vndb,
    "web_search": web_search,
    "read_webpage": read_webpage,
    "save_evidence": save_evidence,
}

tools = [
    {
        "type": "function",
        "function": {
            "name": "search_vndb",
            "description": "Search VNDB for information about a visual novel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The title of the visual novel to search for.",
                    },
                    "results": {
                        "type": "integer",
                        "description": "Maximum number of search results to return.",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vndb",
            "description": (
                "Get detailed information about a specific visual novel using its exact VNDB ID. "
                "Use this after search_vndb when the target visual novel has been identified."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vn_id": {
                        "type": "string",
                        "description": "The exact VNDB visual novel ID, for example v2002.",
                    }
                },
                "required": ["vn_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public web for current or external information. "
                "Use this when structured tools such as VNDB do not contain enough information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The web search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of search results.",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_webpage",
            "description": (
                "Read and extract the main content of a specific public webpage. "
                "Use this after web_search when an important factual claim should "
                "be verified against the original source."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The exact webpage URL to read.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_evidence",
            "description": (
                "Save an important evidence source discovered during research. "
                "Choose a source_ref explicitly provided by a previous tool result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_ref": {
                        "type": "string",
                        "description": "A source ref such as obs_2:r2.",
                    },
                    "claim": {
                        "type": "string",
                        "description": "The factual claim supported by this source.",
                    },
                },
                "required": ["source_ref", "claim"],
            },
        },
    },
]

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
)

messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": ORIGINAL_QUESTION},
]

research_end_reason = "budget_exhausted"

for step in range(MAX_STEPS):
    print(f"\n=== STEP {step + 1} ===")

    response = client.chat.completions.create(
        model=os.getenv("LLM_MODEL"),
        messages=messages,
        tools=tools,
    )

    choice = response.choices[0]
    message = choice.message

    print("=== MODEL ===")
    print("finish_reason:", choice.finish_reason)

    if not message.tool_calls:
        research_end_reason = "model_finished"
        break

    messages.append(
        {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in message.tool_calls
            ],
        }
    )

    for tool_call in message.tool_calls:
        print("=== TOOL CALL ===")
        print("id:", tool_call.id)
        print("name:", tool_call.function.name)
        print("arguments:", tool_call.function.arguments)

        try:
            arguments = json.loads(tool_call.function.arguments)
            tool = tool_registry.get(tool_call.function.name)

            if tool is None:
                result = {
                    "error": "unknown_tool",
                    "message": f"Unknown tool called: {tool_call.function.name}",
                }
            else:
                result = tool(**arguments)

                if isinstance(result, BaseModel):
                    result = result.model_dump()

        except json.JSONDecodeError as error:
            result = {
                "error": "invalid_arguments",
                "message": f"Tool arguments are not valid JSON: {error}",
            }
        except TypeError as error:
            result = {
                "error": "invalid_arguments",
                "message": str(error),
            }
        except Exception as error:
            result = {
                "error": "tool_execution_failed",
                "message": str(error),
            }

        observation_counter += 1
        observation_id = f"obs_{observation_counter}"

        tool_observations[observation_id] = {
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.function.name,
            "data": result,
        }

        visible_data = result

        if (
            tool_call.function.name == "web_search"
            and isinstance(result, dict)
            and "error" not in result
        ):
            visible_data = register_web_search_sources(
                observation_id,
                result,
            )

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(
                    {
                        "observation_id": observation_id,
                        "data": visible_data,
                    },
                    ensure_ascii=False,
                ),
            }
        )

print(f"\n=== RESEARCH ENDED: {research_end_reason} ===")
print("Generating final answer from evidence memory...")

final_messages = [
    {"role": "system", "content": FINAL_SYNTHESIS_PROMPT},
    {
        "role": "user",
        "content": (
            f"Original question:\n{ORIGINAL_QUESTION}\n\n"
            f"Evidence:\n{build_evidence_context()}"
        ),
    },
]

final_response = client.chat.completions.create(
    model=os.getenv("LLM_MODEL"),
    messages=final_messages,
)

print("=== FINAL ANSWER ===")
print(final_response.choices[0].message.content)

print("\n=== EVIDENCE STORE ===")
for evidence in evidence_store:
    print(evidence.model_dump_json(indent=2))
