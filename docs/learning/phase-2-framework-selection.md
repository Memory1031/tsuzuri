# Phase 2 — Framework Selection

Phase 2 introduces a real agent SDK after the raw Phase 0/1 implementation made the underlying mechanics concrete.

The selected framework for Tsuzuri is **PydanticAI**.

The objective is not to collect frameworks. It is to choose the smallest framework that can realistically remain useful as Tsuzuri grows from research into personal context, MCP, approved write actions, and a React UI.

## Decision

Use **PydanticAI** as Tsuzuri's primary agent SDK.

Keep **LangGraph** as a future orchestration option only if Tsuzuri develops workflows complex enough to need an explicit graph/state-machine model.

Treat **Google ADK** as a framework worth learning separately, especially for Google Cloud / Vertex-oriented production systems, but not as the default Tsuzuri runtime.

Treat **OpenAI Agents SDK** as a useful minimal reference implementation and a strong option for OpenAI-first systems, but Tsuzuri should remain model-provider neutral.

## Why PydanticAI fits Tsuzuri

Tsuzuri is intended to remain:

```text
local-first
Python backend
React frontend
SQLite persistence
Bangumi / VNDB / Steam / Web tools
MCP integrations
multiple model providers
optional Electron shell
```

PydanticAI fits this shape because it is Pythonic, type-driven, model-agnostic, and relatively small in conceptual surface area.

It also aligns with concepts already used in Phase 1:

```text
Pydantic BaseModel
Field / validation
Python functions
provider adapters
explicit domain models
```

The migration should therefore make framework value visible instead of replacing the project with a large new vocabulary.

A useful rough analogy is:

```text
PydanticAI ≈ FastAPI for agents

"Give me clean Python primitives for building the application."
```

This is only an analogy, but it captures the desired developer experience: typed Python APIs, low ceremony, and freedom to organize the application around Tsuzuri's own domain.

## Why not LangGraph first?

LangGraph is powerful, but its main strength is explicit orchestration:

```text
State
 ↓
Node
 ↓
Conditional Edge
 ↓
Checkpoint / Interrupt / Resume
```

That is valuable when the workflow itself becomes a major source of complexity—for example, long-lived tasks with branching, repeated pauses, retries, approvals, recovery, or multiple cooperating agents.

Tsuzuri does not have that complexity yet.

Using LangGraph now would risk moving the learning focus from ACGN product capabilities to graph/workflow engineering.

A better rule is:

> Add LangGraph only when ordinary agent/tool code becomes harder to understand than an explicit workflow graph would be.

A rough analogy is that LangGraph is closer to a workflow/state-machine runtime than to a normal Python web framework.

## Why not Google ADK as the primary SDK?

Google ADK is a capable general agent framework and can run without making every business integration a Google service.

However, much of its strongest production differentiation is naturally aligned with the broader Google agent and cloud ecosystem: deployment, managed runtime, Google Cloud integrations, observability, and related platform services.

Tsuzuri currently has no planned dependency on GCP or Vertex AI. Its production target is closer to a local Python service plus React/SQLite than to a managed cloud agent platform.

Therefore ADK remains useful to understand, but Tsuzuri would be adopting more platform-shaped abstractions than it currently needs.

## Why not OpenAI Agents SDK as the primary SDK?

OpenAI Agents SDK is intentionally small and is a very good conceptual comparison with the raw loop.

It is especially attractive for OpenAI-first products because its core abstractions and hosted capabilities line up naturally with the OpenAI platform.

Tsuzuri, however, intentionally uses provider-neutral model configuration and may use GLM, OpenAI, Gemini, Claude, OpenRouter, or local models over time.

PydanticAI therefore fits the project's provider-neutral direction better as the default long-term dependency.

## UI and AG-UI clarification

PydanticAI support for AG-UI or other UI/event adapters does **not** mean Tsuzuri must use a prebuilt frontend.

AG-UI is best thought of as an optional agent-to-frontend event protocol, not a visual component library.

Tsuzuri can still build its React UI completely from scratch:

```text
React UI (owned by Tsuzuri)
        ↑
custom SSE or optional AG-UI events
        ↑
FastAPI / Python service
        ↑
PydanticAI agent
```

Phase 2 will not introduce AG-UI. UI transport remains a Phase 5 concern.

## Durable execution clarification

PydanticAI can integrate with durable workflow systems such as Temporal, DBOS, Prefect, or Restate, but Tsuzuri does **not** need them now.

Durable execution becomes useful when a task must survive process restarts or wait for a long period, for example:

```text
research complete
 ↓
action prepared
 ↓
wait for user approval for hours/days
 ↓
process restarts
 ↓
resume from the pending action
```

For near-term Tsuzuri, normal runtime state and later SQLite persistence are sufficient.

The important point is architectural freedom: choosing PydanticAI does not prevent adding a durable workflow layer later if a real requirement appears.

## Phase 2 migration plan

Phase 2 should rebuild existing behavior before adding new product scope.

The order is deliberate:

```text
P2.1  Rebuild the existing research agent
 ↓
P2.2  Compare explicit save_evidence with a runtime-guaranteed Evidence Pipeline
 ↓
P2.3  Experiment with Planning only for research tasks that actually benefit from it
 ↓
P2.4  Validate an approval-gated mock write action
```

The goal is to introduce one abstraction at a time and keep each experiment tied to a problem already observed in the raw runtime.

### P2.1 — Rebuild Phase 1 research

Migrate:

```text
search_vndb
get_vndb
web_search
read_webpage
```

Compare raw code with PydanticAI for:

- tool schema generation;
- tool registration;
- tool dispatch;
- message history;
- model/provider configuration;
- dependency/context injection;
- streaming events;
- errors and retries;
- code size and testability.

Keep the first migration behaviorally close to Phase 1. Do not introduce planning, automatic evidence extraction, multi-agent research, or UI transport at the same time.

### P2.2 — Compare Evidence collection models

Phase 1 deliberately made the research model call `save_evidence(...)` itself. This exposed the distinction between a Tool Result and durable Evidence, but it also created a failure mode: important information is lost if the model forgets to save it.

Compare two designs:

```text
A. Model-driven Evidence

Tool Result
  ↓
Research Agent decides it matters
  ↓
save_evidence(source_ref, claim)
  ↓
Evidence Store
```

and:

```text
B. Runtime-guaranteed Evidence Pipeline

Tool Result
  ↓
Runtime always captures Raw Observation / Evidence Candidate
  ↓
semantic extractor / policy decides relevance and claim
  ↓
Evidence Store
```

The second design does **not** mean the runtime magically understands source semantics. The runtime should own mechanical facts it already knows—tool identity, URL, source text, observation identity, timestamps—while an LLM or explicit policy still performs semantic judgments such as relevance and claim extraction.

This experiment should answer:

- Should `save_evidence` remain a model-facing tool?
- Which parts can be guaranteed mechanically after tool execution?
- Does automatic extraction improve recall enough to justify extra model calls and cost?
- Should raw observations, evidence candidates, and selected Evidence remain separate layers?
- Which lifecycle hooks are useful without hiding Tsuzuri's Evidence semantics inside the SDK?

Do not optimize for maximum automation. The objective is to find the smallest reliable Evidence boundary.

### P2.3 — Experiment with Planning only when useful

Do not make every research request create a plan.

First test a simple question that should remain direct:

```text
When did the original STEINS;GATE launch on Steam?
```

Then test a broader question where explicit decomposition may help:

```text
Compare the original STEINS;GATE, ELITE, and RE:BOOT across release history,
content differences, and available PC versions.
```

For complex research, a plan may look like:

```text
1. resolve the three target works
2. collect structured release data
3. verify official store information
4. investigate content differences
5. resolve conflicts
6. synthesize the answer
```

The framework may provide plan storage, updates, events, and persistence, but Tsuzuri/the model still owns the semantic research strategy:

```text
Framework / Runtime owns
- plan container
- task status
- persistence / restoration
- update mechanics
- events

Research policy / model owns
- how the question is decomposed
- source priority
- which task should run next
- when evidence is sufficient
```

The experiment should answer whether explicit Planning improves complex research enough to justify the extra state and tokens. If not, keep the normal agent loop as the default.

### P2.4 — Validate future action support early

Do not wait until the real Bangumi write integration to discover whether the SDK fits actions.

Introduce a fake write tool such as:

```text
set_mock_rating(work, rating)
```

Then test:

```text
resolve target
 ↓
read current state
 ↓
propose write
 ↓
approval required
 ↓
approve / reject
 ↓
execute
 ↓
read state again
 ↓
verify
```

This is an SDK validation exercise, not the real P4 account integration.

### Keep domain semantics outside the SDK

The SDK may own generic runtime concerns, but Tsuzuri should continue to own:

- ACGN entity resolution;
- source/evidence semantics;
- source-quality policy;
- research strategy;
- collection/action policy;
- read vs write safety rules;
- product-specific confirmation UX;
- external provider adapters.

A useful rule for both Evidence and Planning is:

> Let the SDK/runtime own generic mechanics; let Tsuzuri own domain meaning and policy.

## Exit criteria

Phase 2 is complete when we can answer:

1. Which parts of the raw P0/P1 runtime disappeared after adopting PydanticAI?
2. Which abstractions became clearer rather than merely shorter?
3. Should Evidence remain model-saved, become runtime-triggered, or use a hybrid approach?
4. For which research questions does an explicit Plan improve quality enough to justify its complexity?
5. Can the same SDK comfortably support research and an approval-gated mock action?
6. Can Tsuzuri stay provider-neutral?
7. Which state, Evidence, Planning, and domain concepts still belong to Tsuzuri rather than the SDK?
8. Is PydanticAI simple enough to keep as a long-term dependency?

If the answer to the last question is no, Phase 2 should produce evidence for changing SDK rather than forcing the original choice.