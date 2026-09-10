# Phase 0 — Understanding the Agent Loop

## Goal

The goal of Phase 0 is to understand what fundamentally makes an agent different from a normal LLM request.

Instead of starting with an agent framework such as LangGraph or ADK, this phase implements the smallest possible agent runtime directly with Python and an OpenAI-compatible chat completion API.

The purpose is not to build a production-ready agent.

The purpose is to understand the execution model underneath higher-level agent frameworks.

---

## 1. From an LLM Call to an Agent

A normal LLM request follows a simple flow:

```text
User
  ↓
Model
  ↓
Text Response
```

The model receives some context and generates a response.

An agent introduces an interaction loop between the model and an external environment:

```text
User
  ↓
Model
  ↓
Action
  ↓
Tool / Environment
  ↓
Observation
  ↓
Model
  ↓
Next Action or Final Answer
```

The important difference is that the model is no longer limited to generating text.

It can decide that additional information or an external action is required before answering.

---

## 2. LLM Client and Model Provider

Tsuzuri currently uses the official OpenAI Python SDK as an API client.

However, using the OpenAI SDK does not necessarily mean using an OpenAI model.

The current request path is:

```text
Tsuzuri
  ↓
OpenAI Python SDK
  ↓
OpenAI-compatible API
  ↓
Configured LLM provider
  ↓
Configured model
```

For example, the current provider may be GLM while still using:

```python
client.chat.completions.create(...)
```

The active provider is determined by configuration such as:

```text
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
```

This creates an important abstraction boundary:

> Tsuzuri depends on an LLM capability and protocol, not directly on a specific model vendor.

The SDK and the model provider should therefore be treated as separate concepts.

---

## 3. Tool Schema vs Tool Implementation

A tool has two different representations in the current runtime.

### Tool Schema

The tool schema is sent to the model.

Example:

```json
{
  "name": "search_vndb",
  "description": "Search VNDB for information about a visual novel.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string"
      }
    },
    "required": ["query"]
  }
}
```

Its purpose is to tell the model:

- what tools are available
- what each tool does
- what arguments each tool accepts

The model only sees this description.

It does not see the Python implementation.

### Tool Implementation

The implementation is the actual executable Python function:

```python
def search_vndb(query: str):
    ...
```

This function belongs to the runtime environment.

The model cannot directly execute it.

### Tool Registry

The runtime connects the schema name to the actual Python callable through a registry:

```python
tool_registry = {
    "search_vndb": search_vndb,
    "search_bangumi": search_bangumi,
}
```

The relationship is:

```text
Tool Schema
     ↓
Model knows a tool exists
     ↓
Tool Call
name = "search_vndb"
     ↓
Tool Registry
     ↓
Python callable
     ↓
search_vndb(...)
```

This distinction becomes increasingly important when tools are later provided through MCP or other external services.

---

## 4. What Tool Calling Actually Means

A model does not execute a tool.

When the model wants to use a tool, it generates a structured action request.

For example:

```json
{
  "name": "search_vndb",
  "arguments": "{\"query\":\"STEINS;GATE\"}"
}
```

This means:

> The model is asking the host runtime to execute `search_vndb` using the provided arguments.

The actual flow is therefore:

```text
LLM
 ↓
Tool Call Request
 ↓
Agent Runtime
 ↓
Parse Arguments
 ↓
Find Tool
 ↓
Execute Python Function
 ↓
Tool Result
```

This is one of the most important observations from Phase 0:

> Tool calling is structured model output. Tool execution belongs to the runtime.

---

## 5. Tool Call IDs

Every tool call contains an identifier.

Example:

```text
call_-7272105206723110283
```

This should be treated as an opaque correlation ID.

Its purpose is to connect a tool result with the exact tool request that produced it.

For example:

```text
Assistant

call_A
→ search_vndb("STEINS;GATE")

call_B
→ search_bangumi("STEINS;GATE")
```

After execution:

```text
Tool Result

tool_call_id = call_A
→ VNDB result

tool_call_id = call_B
→ Bangumi result
```

This becomes particularly important when:

- multiple tools are requested in one model response
- the same tool is called multiple times
- tools are executed concurrently
- results return in a different order from requests

The ID should not be interpreted or parsed.

It should simply be preserved and returned to the model unchanged.

---

## 6. Messages as the First Agent State

The Chat Completions API should be treated as stateless for the purposes of this implementation.

Previous conversation history is not automatically remembered by the model API.

The runtime must explicitly maintain it.

The simplest state representation is therefore the `messages` list:

```text
messages

user
 ↓
assistant / tool_calls
 ↓
tool / result
 ↓
assistant / tool_calls
 ↓
tool / result
 ↓
...
```

A simple interaction may look like:

```text
[0] user
    "Search VNDB for STEINS;GATE."

[1] assistant
    tool_calls:
        search_vndb(...)

[2] tool
    tool_call_id: call_A
    content: {...}

[3] assistant
    final answer
```

At this stage:

> Agent State ≈ Messages

This is intentionally simple.

Later frameworks may introduce richer state structures, checkpoints, persistence, summaries, or memory, but those concepts build on the same underlying requirement:

> The runtime must preserve the information needed for the model's next decision.

---

## 7. The Agent Loop

The core of the Phase 0 implementation is the agent loop.

Conceptually:

```text
             ┌──────────────────┐
             │                  │
             ▼                  │
           Model                │
             │                  │
      Has tool calls?           │
        │        │              │
       Yes       No             │
        │        │              │
        ▼        ▼              │
      Tools   Final Answer      │
        │                       │
        └───────────────────────┘
```

A simplified implementation looks like:

```python
for step in range(MAX_STEPS):
    response = call_model()

    if response_has_tool_calls:
        execute_tools()
        add_results_to_context()
        continue

    return final_answer
```

The model decides what should happen next.

The runtime provides the mechanism that allows this decision process to continue.

This leads to another important observation:

> The model provides decisions, while the runtime provides execution and iteration.

Without the runtime loop, tool calling would stop after a single model response.

---

## 8. Multiple Tool Calls

A single model response may contain multiple tool calls.

For example:

```text
assistant
├── search_vndb("STEINS;GATE")
└── search_bangumi("STEINS;GATE")
```

These are different from multiple model choices.

The distinction is:

```text
choices[]
→ multiple candidate model responses

tool_calls[]
→ multiple actions requested by one model response
```

Tsuzuri does not currently need multiple model choices.

However, multiple tool calls are important because a research task may naturally require several independent data sources.

The runtime therefore processes:

```python
for tool_call in message.tool_calls:
    ...
```

The original assistant message containing all tool calls should be preserved as one message.

The results are then appended separately:

```text
user

assistant
├── call_A
└── call_B

tool
└── result for call_A

tool
└── result for call_B
```

This accurately represents what happened during the model step.

---

## 9. Errors Are Observations

Tool execution is not guaranteed to succeed.

Failures may occur at several levels.

### Invalid model output

Examples:

```text
invalid JSON
missing argument
wrong argument name
wrong argument type
```

### Tool execution failure

Examples:

```text
network timeout
HTTP 500
rate limit
service unavailable
```

### Domain failure

Examples:

```text
work not found
ambiguous search result
insufficient permissions
no matching release
```

Instead of immediately crashing the entire agent, many recoverable errors can be represented as tool results:

```json
{
  "error": "invalid_arguments",
  "message": "Missing required argument: query"
}
```

The result is then returned to the model like any other observation:

```text
Model
 ↓
Invalid Action
 ↓
Runtime
 ↓
Error Observation
 ↓
Model
 ↓
Retry / Change Strategy / Give Up
```

This creates the possibility of self-correction.

A key lesson from Phase 0 is:

> Tool failure does not necessarily mean agent failure.

Failures can become information that influences the next model decision.

---

## 10. Execution Budgets and Termination

An agent must not be allowed to execute indefinitely.

A model may repeatedly:

- call the same tool
- retry invalid arguments
- alternate between tools
- fail to reach a final answer

The Phase 0 runtime introduces a simple execution budget:

```python
MAX_STEPS = 5
```

If the model cannot complete the task within the allowed number of steps, the runtime terminates the run.

This is the simplest form of agent resource control.

More advanced runtimes may eventually include:

```text
max_steps
max_tokens
max_cost
timeout
max_tool_calls
```

Execution budgets protect against:

```text
infinite loops
unbounded retries
unexpected API cost
stuck agents
runaway tool usage
```

Termination is therefore a responsibility of the runtime, not only the model.

---

## 11. `finish_reason` vs `tool_calls`

The two fields describe different things.

### `tool_calls`

Describes:

> What action does the model want the runtime to execute?

### `finish_reason`

Describes:

> Why did this particular model generation stop?

Typical values may include:

```text
stop
tool_calls
length
content_filter
```

The Phase 0 implementation uses the simplified condition:

```python
if message.tool_calls:
    ...
else:
    # final answer
```

This is sufficient for understanding the basic loop.

However, a production runtime should distinguish between cases such as:

```text
stop
→ normal completion

tool_calls
→ waiting for tools

length
→ generation was truncated

other reasons
→ provider-specific handling may be required
```

Therefore:

> No tool call does not always mean successful completion.

This limitation is intentionally left for later phases.

---

## 12. Context, Cache, and Compaction

These three concepts should be kept separate.

### Context

Context is the information actually provided to the model for the current request.

It may include:

```text
system instructions
conversation messages
tool schemas
tool results
retrieved knowledge
current user input
```

### Cache

Provider-side prompt caching is an optimization.

If two requests share a large identical prefix, the provider may reuse previous computation.

Cache does not mean the model remembers previous conversations.

Even when cached, the relevant context still needs to be included in the request.

Therefore:

> Cache ≠ Memory.

### Context Compaction

As an agent runs, messages and tool results may become too large for the model context window.

Compaction is a runtime responsibility.

The general flow is:

```text
Build Context
     ↓
Estimate Context Budget
     ↓
Enough space?
 ├─ Yes → Call Model
 └─ No
      ↓
   Compact Context
      ↓
   Recalculate Budget
      ↓
   Call Model
```

Compaction may include:

```text
dropping unnecessary tool fields
truncating large search results
summarizing old conversation history
preserving recent messages
extracting structured memory
```

For example:

```text
Before

user
assistant
tool result (large)
assistant
tool result (large)
assistant
tool result (large)
...
```

may become:

```text
system
compressed summary of old research
recent messages
current tool results
```

This process is usually lossy.

The original details are no longer visible to the model unless they are stored elsewhere and retrieved again.

An important design rule is therefore:

> Not all context has equal value.

Information such as user constraints or verified entity IDs may deserve higher retention priority than old search snippets or large raw web pages.

Phase 0 does not implement compaction.

It only establishes the mental model needed for later context engineering.

---

## 13. Grounding: A First Important Observation

One of the most useful observations during Phase 0 came from the mock VNDB tool.

The tool returned only:

```json
{
  "id": "v2002",
  "title": "STEINS;GATE",
  "release": "2009-10-15"
}
```

However, the model's final answer also included information about:

```text
developers
Science Adventure
D-Mail
anime adaptation
general reputation
```

None of those facts were present in the tool result.

This demonstrates that:

> Calling a tool does not mean the final answer is grounded exclusively in that tool.

The model can combine:

```text
user request
+
tool observations
+
parametric knowledge
```

when generating the final answer.

This becomes a major concern for a research-oriented agent.

Later phases will need to distinguish:

```text
verified evidence
model background knowledge
inference
unsupported claims
```

This naturally leads into concepts such as:

```text
grounding
source provenance
citations
evidence tracking
confidence
```

These are major goals of Phase 1.

---

## 14. What Was Intentionally Implemented by Hand

Phase 0 deliberately avoids agent frameworks.

The following mechanisms were implemented directly:

```text
LLM request
tool schema definition
tool selection handling
tool argument parsing
tool registry
tool dispatch
multiple tool calls
tool result propagation
agent loop
error observations
execution step limit
conversation state
```

This is important because future abstractions can now be evaluated against a known baseline.

When LangGraph, ADK, or another framework is introduced later, the question will not simply be:

> How do I use this framework?

Instead, the useful question becomes:

> Which parts of the runtime I built manually does this framework now manage for me?

---

## 15. Current Limitations

Phase 0 intentionally leaves many production concerns unresolved.

These are not considered failures of the phase.

They are the reasons future phases exist.

Current limitations include:

- tools return mock data
- tool schemas are manually defined
- tool registration is manually maintained
- argument validation is minimal
- tool execution is sequential
- broad exception handling is still used
- `finish_reason` handling is incomplete
- context grows without compaction
- conversation state is not persisted
- there is no real authentication
- there is no tracing system
- there is no evaluation framework
- there is no evidence model
- final answers are not fully grounded
- there is no MCP integration
- there is no RAG
- there is no frontend

The goal of Phase 0 was not to solve these problems.

It was to understand the mechanism underneath them.

---

## 16. Phase 0 Mental Model

The most important mental model from this phase is:

```text
                    Agent Runtime

User
  │
  ▼
Context / Messages
  │
  ▼
Model
  │
  ├─────────────── Final Answer ───────────────→ End
  │
  ▼
Tool Call(s)
  │
  ▼
Argument Parsing / Validation
  │
  ▼
Tool Registry
  │
  ▼
Tool Implementation
  │
  ▼
Result / Error Observation
  │
  ▼
Context / Messages
  │
  └──────────────────────────────→ Model again
```

The model is responsible for choosing actions.

The runtime is responsible for:

```text
execution
state
validation
tool dispatch
error handling
resource limits
iteration
termination
```

The tools represent the external environment available to the model.

Together, these components create the basic agent system.

---

## 17. Phase 0 Exit Questions

Phase 0 is considered complete when the following questions can be answered without relying on framework-specific terminology.

### Does the LLM execute Python functions?

No.

The model generates a structured tool call request. The runtime executes the Python function.

### Why does the model need a tool schema?

The schema describes the available capability, its purpose, and its expected arguments.

### Why is a tool registry still needed?

The schema is only a description. The runtime needs a mapping from the tool name generated by the model to an actual executable callable.

### Why must the tool result be sent back to the model?

The model has no direct access to local Python execution. The result must become part of the next model context.

### Why does a tool call have an ID?

The ID correlates a specific tool request with its corresponding result.

### Who creates the agent loop?

The runtime.

The model makes one decision per model request. The runtime decides whether to execute tools and invoke the model again.

### Why is an execution limit necessary?

To prevent infinite loops, unbounded retries, runaway tool usage, and uncontrolled cost.

### Can a tool error be returned to the model?

Yes.

A recoverable failure can become an observation that allows the model to retry or choose another strategy.

### Where is the current agent state?

In the Phase 0 implementation, most state is represented by the `messages` list.

### Is tool calling the same thing as an agent?

No.

Tool calling is one mechanism for expressing actions.

An agent also requires a runtime, state management, an execution loop, environment interaction, and termination logic.

---

## 18. Next Phase

Phase 1 moves Tsuzuri from a mock environment into the real world.

The first step is to replace:

```python
def search_vndb(...):
    return MOCK_DATA
```

with the real VNDB Kana API.

The focus changes from:

> Can the model call a tool?

to:

> Can the agent investigate a real question reliably?

Real external tools will introduce new problems such as:

```text
HTTP requests
API schemas
empty results
multiple matching entities
timeouts
rate limits
data normalization
source provenance
conflicting evidence
grounding
```

These problems form the basis of the next stage:

# Phase 1 — Building a Research Agent
