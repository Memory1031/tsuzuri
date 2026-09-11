import json
import os
import secrets
import sys
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from tsuzuri.models.evidence import Evidence, EvidenceKind, EvidenceSource
from tsuzuri.tools.vndb import get_vndb, search_vndb
from tsuzuri.tools.web import read_webpage, web_search

evidence_store: list[Evidence] = []

observation_counter = 0

tool_observations: dict[str, dict[str, Any]] = {}

evidence_sources: dict[str, EvidenceSource] = {}

load_dotenv()


def register_web_search_sources(
    observation_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    results = result.get("results", [])

    visible_results = []

    for index, item in enumerate(results, start=1):
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


def save_evidence(
    source_ref: str,
    claim: str,
) -> Evidence:
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


class CitationSource(BaseModel):
    evidence_id: str

    source_name: str
    source_url: str | None = None
    support: str


def build_synthesis_context(
    evidence_store: list[Evidence],
) -> tuple[str, dict[str, CitationSource]]:
    response_nonce = secrets.token_hex(4)

    citation_registry: dict[str, CitationSource] = {}
    context_items = []

    for index, evidence in enumerate(evidence_store, start=1):
        token = f"[[TSUZURI_CITE:{response_nonce}:{index}]]"

        citation_registry[token] = CitationSource(
            evidence_id=evidence.id,
            source_name=evidence.source_name,
            source_url=evidence.source_url,
            support=evidence.support,
        )

        context_items.append(
            {
                "citation_token": token,
                "source_name": evidence.source_name,
                "source_url": evidence.source_url,
                "claim": evidence.claim,
                "support": evidence.support,
            }
        )

    context = json.dumps(
        context_items,
        ensure_ascii=False,
        indent=2,
    )

    return context, citation_registry


class CitationStreamParser:
    MARKER_PREFIX = "[[TSUZURI_CITE:"

    def __init__(
        self,
        citation_registry: dict[str, CitationSource],
    ):
        self.citation_registry = citation_registry
        self.public_numbers: dict[str, int] = {}
        self.used_tokens: list[str] = []
        self.buffer = ""

    def feed(self, delta: str) -> list[dict[str, Any]]:
        self.buffer += delta

        events = []

        while self.buffer:
            marker_start = self.buffer.find(self.MARKER_PREFIX)

            if marker_start == -1:
                keep_length = self._partial_prefix_length(self.buffer)

                if keep_length > 0:
                    text = self.buffer[:-keep_length]

                    if text:
                        events.append(
                            {
                                "type": "content_delta",
                                "text": text,
                            }
                        )

                    self.buffer = self.buffer[-keep_length:]
                else:
                    events.append(
                        {
                            "type": "content_delta",
                            "text": self.buffer,
                        }
                    )
                    self.buffer = ""

                break

            if marker_start > 0:
                events.append(
                    {
                        "type": "content_delta",
                        "text": self.buffer[:marker_start],
                    }
                )

                self.buffer = self.buffer[marker_start:]

            marker_end = self.buffer.find("]]")

            if marker_end == -1:
                break

            token = self.buffer[: marker_end + 2]
            self.buffer = self.buffer[marker_end + 2 :]

            source = self.citation_registry.get(token)

            if source is None:
                events.append(
                    {
                        "type": "citation_error",
                        "token": token,
                    }
                )
                continue

            number = self.public_numbers.get(token)

            if number is None:
                number = len(self.public_numbers) + 1
                self.public_numbers[token] = number
                self.used_tokens.append(token)

            events.append(
                {
                    "type": "citation",
                    "number": number,
                    "source": source,
                }
            )

        return events

    def flush(self) -> list[dict[str, Any]]:
        if not self.buffer:
            return []

        remaining = self.buffer
        self.buffer = ""

        looks_like_protocol = remaining.startswith(
            self.MARKER_PREFIX
        ) or self.MARKER_PREFIX.startswith(remaining)

        if looks_like_protocol:
            return [
                {
                    "type": "citation_error",
                    "token": remaining,
                }
            ]

        return [
            {
                "type": "content_delta",
                "text": remaining,
            }
        ]

    def _partial_prefix_length(self, text: str) -> int:
        max_length = min(
            len(text),
            len(self.MARKER_PREFIX) - 1,
        )

        for length in range(max_length, 0, -1):
            if text.endswith(self.MARKER_PREFIX[:length]):
                return length

        return 0


def emit_citation_events(events: list[dict[str, Any]]) -> None:
    for event in events:
        if event["type"] == "content_delta":
            print(
                event["text"],
                end="",
                flush=True,
            )

        elif event["type"] == "citation":
            print(
                f"[{event['number']}]",
                end="",
                flush=True,
            )

        elif event["type"] == "citation_error":
            print(
                f"\n[warning] invalid citation: {event['token']}",
                file=sys.stderr,
            )


tool_registry = {
    "search_vndb": search_vndb,
    "get_vndb": get_vndb,
    "web_search": web_search,
    "read_webpage": read_webpage,
    "save_evidence": save_evidence,
}

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
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
Before ending the research phase, make sure all facts that are necessary
to answer the user's question have been saved as evidence.

The final answer will be generated from saved evidence only.
Information that is not saved as evidence will not be available during
final synthesis.
""".strip()

FINAL_SYNTHESIS_PROMPT = """
You are producing the final answer for an ACGN research task.

The research phase has ended.

Answer the user's question using only the provided evidence.

Write normal Markdown.

For factual claims supported by evidence:
- Insert the exact citation_token belonging to the supporting evidence
  immediately after the relevant claim.
- Only use citation tokens explicitly provided in the evidence context.
- Never invent or modify a citation token.
- Multiple citation tokens may follow the same claim when multiple sources support it.
- Do not mention internal evidence IDs or explain the citation protocol.

If evidence conflicts, explain the conflict.
If evidence is insufficient, explicitly say so.
""".strip()

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
                "Use this when structured tools such as VNDB do not contain "
                "enough information to answer the question."
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
                "Choose one of the source refs explicitly provided in previous "
                "tool results. Save only evidence that materially supports "
                "the answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_ref": {
                        "type": "string",
                        "description": (
                            "An exact evidence source ref provided by a previous "
                            "tool result, for example obs_2:r2."
                        ),
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

MAX_STEPS = 5

ORIGINAL_QUESTION = (
    "请确认原版 STEINS;GATE 是什么时候登陆 Steam 的。"
    "必须基于工具查询结果回答，不要使用未经验证的模型背景知识。"
)

messages = [
    {
        "role": "system",
        "content": SYSTEM_PROMPT,
    },
    {
        "role": "user",
        "content": ORIGINAL_QUESTION,
    },
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

        visible_result = {
            "observation_id": observation_id,
            "data": visible_data,
        }

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(
                    visible_result,
                    ensure_ascii=False,
                ),
            }
        )


print(f"\n=== RESEARCH ENDED: {research_end_reason} ===")
print("Generating final answer from evidence memory...")

print("\n=== EVIDENCE STORE (internal, hidden from the model) ===")

for evidence in evidence_store:
    print(evidence.model_dump_json(indent=2))

evidence_context, citation_registry = build_synthesis_context(evidence_store)

final_messages = [
    {
        "role": "system",
        "content": FINAL_SYNTHESIS_PROMPT,
    },
    {
        "role": "user",
        "content": (
            f"Original question:\n"
            f"{ORIGINAL_QUESTION}\n\n"
            f"Evidence:\n"
            f"{evidence_context}"
        ),
    },
]

stream = client.chat.completions.create(
    model=os.getenv("LLM_MODEL"),
    messages=final_messages,
    stream=True,
)

parser = CitationStreamParser(citation_registry)

print("\n=== FINAL ANSWER ===")

for chunk in stream:
    if not chunk.choices:
        continue

    delta = chunk.choices[0].delta.content

    if not delta:
        continue

    emit_citation_events(parser.feed(delta))

emit_citation_events(parser.flush())

print()

print("\n=== SOURCES ===")

for token in parser.used_tokens:
    source = citation_registry[token]
    number = parser.public_numbers[token]

    print(f"[{number}] {source.source_name}")

    if source.source_url:
        print(f"    {source.source_url}")

    print(f"    {source.support[:300]}")
