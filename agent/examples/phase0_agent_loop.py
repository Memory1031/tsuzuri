"""
Phase 0 reference implementation.

A minimal raw agent loop demonstrating:
- tool schemas
- tool dispatch
- multiple tool calls
- tool result propagation
- error handling
- bounded execution

All external tools use mock data.
"""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def search_vndb(query: str):
    return {
        "id": "v2002",
        "title": "STEINS;GATE",
        "release": "2009-10-15",
    }


def search_bangumi(query: str):
    return {
        "id": "10380",
        "title": "Steins;Gate",
        "rating": 8.7,
    }


tool_registry = {
    "search_vndb": search_vndb,
    "search_bangumi": search_bangumi,
}

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
)

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
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_bangumi",
            "description": "Search Bangumi for information about an ACGN work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The title of the work to search for.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

MAX_STEPS = 5

messages = [
    {
        "role": "user",
        "content": "请分别查询 VNDB 和 Bangumi，告诉我 STEINS;GATE 在两个数据库中的信息。",
    }
]

for step in range(MAX_STEPS):
    print(f"\n=== STEP {step + 1} ===")
    response = client.chat.completions.create(
        model=os.getenv("LLM_MODEL"),
        messages=messages,
        tools=tools,
    )

    message = response.choices[0].message

    if not message.tool_calls:
        print("=== FINAL ANSWER ===")
        print(message.content)
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

        print("=== TOOL RESULT ===")
        print(result)

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )

else:
    raise RuntimeError(f"Agent exceeded max steps: {MAX_STEPS}")
