# Learning & Evolution Roadmap

Tsuzuri is intentionally designed as a learning project that becomes useful through gradual capability growth.

The roadmap is organized around one principle:

> Add one new agent concept at a time, and make every new concept solve a real ACGN use case.

## Current progress

```text
P0  Raw Agent Loop                 ✅ complete
 ↓
P1  Research + Evidence            ✅ complete
 ↓
P2  PydanticAI Comparison          ← current
 ↓
P3  Personal Context (Read)
 ↓
P4  MCP + Safe Actions
 ↓
P5  React + Vite UI
 ↓
P6  SQLite + Entity Resolution
 ↓
P7  RAG
 ↓
P8  Eval + Observability
 ↓
P9  Optional Electron Desktop
```

---

## Phase 0 — Understand the Agent Loop ✅

### Goal

Understand what makes an agent different from a single LLM request.

### Product capability

A CLI-based ACGN research assistant that can answer simple questions using a few explicit tools.

### Technical scope

- Python CLI
- one LLM provider
- raw function/tool calling
- explicit message/context loop
- minimal tool registry
- no agent framework
- no database
- no frontend

### Initial tools

```text
search_bangumi
search_vndb
web_search
```

### What should be learned

- how tool schemas are presented to the model
- how the model chooses a tool
- how tool arguments are validated
- how tool results are inserted back into context
- why the model chooses to continue or stop
- how failures affect the loop
- the difference between an LLM call and an agent run

### Exit criteria

You can explain the full execution lifecycle without relying on framework terminology.

**Status:** complete. See [`docs/learning/phase-0-agent-loop.md`](learning/phase-0-agent-loop.md).

---

## Phase 1 — Build a Research Agent ✅

### Goal

Move from “call an API” to “investigate a question”.

### Product capability

Tsuzuri can research questions such as:

- Which version of a visual novel is this?
- How does a Steam release differ from the original edition?
- How far did an anime adaptation cover the source material?
- What evidence supports a newly announced title rumor?

### New capabilities

- multiple tool calls per run
- real structured VNDB integration
- web search + webpage reading
- source provenance
- runtime-owned source references
- evidence collection
- research termination
- uncertainty/conflict handling
- evidence-only final synthesis

### Tools explored

```text
search_vndb
get_vndb
web_search
read_webpage
save_evidence
```

### What should be learned

- tool routing
- Search → Resolve → Get
- iterative research
- when structured APIs are better than web search
- why search discovery and webpage reading are different operations
- when the agent has enough evidence to stop
- source quality and conflicting evidence
- Agent State vs model Context
- observations vs durable Evidence
- context rebuilding from selected research memory
- why the model should select runtime-owned refs instead of regenerating IDs/URLs/source text

### Exit criteria

The agent produces answers that are observably better than a single LLM response because it gathered evidence itself, and final synthesis can operate from selected Evidence rather than the complete raw research transcript.

**Status:** complete. See [`docs/learning/phase-1-research-evidence.md`](learning/phase-1-research-evidence.md).

---

## Phase 2 — Introduce an Agent SDK Deliberately ← Current

### Goal

Learn what a real agent SDK removes after understanding the raw loop, and validate that the selected SDK can support Tsuzuri's future research **and** action workflows.

### Selected SDK

**PydanticAI**

See [`docs/learning/phase-2-framework-selection.md`](learning/phase-2-framework-selection.md) for the decision rationale.

The choice is based on Tsuzuri's expected shape:

```text
local-first
Python backend
React frontend
SQLite
Bangumi / VNDB / Steam / Web
MCP
multiple model providers
optional Electron shell
```

PydanticAI is preferred because it is Pythonic, type-driven, provider-neutral, and small enough that Tsuzuri's domain logic remains visible.

### Why the alternatives are not the default

- **LangGraph:** keep as a future orchestration layer if explicit graph/state-machine complexity, long-lived branching workflows, or checkpoint-heavy execution genuinely appears.
- **Google ADK:** strong general framework, especially attractive when using Google's managed agent/cloud platform, but Tsuzuri currently has no planned GCP/Vertex dependency.
- **OpenAI Agents SDK:** excellent minimal SDK and useful reference, but Tsuzuri intentionally wants a provider-neutral long-term core.

### Phase 2 sequence

```text
P2.1  Raw P1 → PydanticAI
      Rebuild the existing research agent without changing behavior first.

P2.2  Evidence Pipeline
      Compare model-driven save_evidence with a runtime-guaranteed
      capture → candidate → semantic extraction pipeline.

P2.3  Planning
      Test explicit planning only on complex research tasks where
      decomposition may actually improve quality.

P2.4  Safe Action
      Validate a mock write flow with approval → execute → verify.
```

The order matters: first reproduce existing behavior, then introduce one new SDK capability at a time.

### P2.1 — Rebuild Phase 1 research

Rebuild the existing workflow with PydanticAI while preserving behavior as much as practical:

```text
search_vndb
get_vndb
web_search
read_webpage
```

Compare:

```text
Raw implementation
vs.
PydanticAI implementation
```

Focus on:

- tool schema generation from Python types;
- tool registration and dispatch;
- execution lifecycle / Agent.run();
- message history;
- RunContext / dependencies;
- provider configuration;
- model switching;
- errors and retries;
- streaming primitives;
- testability;
- code complexity.

Do **not** add Planning, automatic Evidence extraction, multi-agent research, AG-UI, or durable execution during this first migration.

### P2.2 — Compare Evidence collection models

Phase 1 intentionally uses a model-facing `save_evidence(source_ref, claim)` tool. That design made the Evidence boundary visible, but it also means important facts can be lost when the model forgets to save them.

Compare two approaches:

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

The runtime should only guarantee the mechanical path. It can fill data it already knows—tool identity, URL, raw support, observation identity—but semantic decisions such as relevance and claim extraction still belong to an LLM or explicit Tsuzuri policy.

Questions to answer:

- Should `save_evidence` remain model-facing?
- Which Evidence fields can the runtime fill mechanically?
- Does automatic extraction improve recall enough to justify extra model calls/cost?
- Should Raw Observation, Evidence Candidate, and selected Evidence remain separate layers?
- Can SDK lifecycle hooks simplify the pipeline without hiding Evidence semantics?

### P2.3 — Experiment with Planning only when useful

Planning should not become mandatory ceremony for every question.

Use a simple research question as the no-plan baseline:

```text
When did the original STEINS;GATE launch on Steam?
```

Then compare with a broader question where decomposition may help:

```text
Compare the original STEINS;GATE, ELITE, and RE:BOOT across release history,
content differences, and available PC versions.
```

For a complex task, an explicit plan may track steps such as:

```text
1. resolve target works
2. collect structured release data
3. verify official store information
4. investigate content differences
5. resolve conflicting claims
6. synthesize
```

The framework may provide plan storage, status updates, persistence, and events. Tsuzuri/the model still owns the semantic strategy: how to decompose the question, which sources to prioritize, what to investigate next, and when evidence is sufficient.

The experiment should answer:

- When does Planning actually improve research quality?
- What extra token/state cost does it add?
- Which plan mechanics can be delegated to the SDK?
- Which planning policy remains Tsuzuri's responsibility?
- Should Planning be opt-in based on task complexity rather than the default?

### P2.4 — Validate future actions early

Do not wait until the real Bangumi integration to discover whether the SDK fits write workflows.

Add a fake write capability such as:

```text
set_mock_rating(work_ref, rating)
```

Exercise an approval-gated flow:

```text
resolve target
  ↓
read current state
  ↓
prepare write
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

This remains an SDK validation exercise. Real account mutation stays in Phase 4.

### What should be learned

The objective is not “learn PydanticAI syntax”.

The objective is to answer:

> Which generic runtime problems does PydanticAI remove, and which Tsuzuri-specific problems remain ours?

In particular, the SDK should not own Tsuzuri's:

- Evidence semantics;
- ACGN entity resolution;
- source-quality policy;
- research strategy;
- provider adapters;
- collection/action rules;
- read/write safety policy;
- product-specific approval UX.

A useful rule for this phase is:

> Let the SDK/runtime own generic mechanics; let Tsuzuri own domain meaning and policy.

### Exit criteria

You can explain:

1. which raw-runtime code disappeared;
2. which abstractions became clearer rather than merely shorter;
3. whether Evidence should remain model-saved, become runtime-triggered, or use a hybrid design;
4. when explicit Planning improves research enough to justify its complexity;
5. whether research and an approval-gated mock action both feel natural;
6. whether model-provider neutrality remains practical;
7. which State/Evidence/Planning/domain concepts Tsuzuri still owns;
8. why PydanticAI should remain—or why evidence justifies replacing it.

---

## Phase 3 — Personal Context (Read Only)

### Goal

Make Tsuzuri personal without introducing dangerous side effects yet.

### Product capability

Tsuzuri understands the user's ACGN history and can use it for research and recommendations.

### Integrations

#### Bangumi

Read:

- collections
- status
- rating
- progress

#### Steam

Read:

- owned games
- playtime
- recently played
- achievements where useful

#### VNDB

Read optional user-list information when a token is configured.

### Example use case

> I have three hours tonight. Pick a story-heavy game I already own, avoiding action-heavy games.

Possible flow:

```text
Steam library
  ↓
Bangumi history
  ↓
VNDB metadata for candidates
  ↓
Agent reasoning
  ↓
Recommendation
```

### What should be learned

- authentication and token handling
- external account integration
- personal context construction
- structured context vs prompt stuffing
- privacy boundaries
- caching and synchronization basics

### Exit criteria

The same query produces meaningfully different results depending on the connected user's library/history.

---

## Phase 4 — MCP and Safe Action Tools

### Goal

Learn MCP and real side effects through a controlled workflow.

### Strategy

- reuse an existing Bangumi MCP implementation when practical
- implement a small VNDB MCP server yourself for learning
- reuse the approval/action pattern validated in Phase 2

### Example VNDB MCP tools

```text
search_vn
get_vn
get_releases
get_my_list
update_my_list
```

### Write capabilities

Allow actions such as:

- mark a Bangumi work as watching/completed
- update Bangumi progress
- submit a rating
- update a VNDB user-list entry

### Safety rule

All write actions require explicit approval by default.

### Example flow

```text
User: I finished X. Mark it completed and give it 8/10.

Agent resolves the work
  ↓
Agent reads current state
  ↓
Action preview
  ↓
User approves
  ↓
MCP/API write
  ↓
Read state again / verify
  ↓
Result + audit entry
```

### What should be learned

- MCP server/tool concepts
- tool schema design
- auth propagation
- read vs write tools
- side effects
- human-in-the-loop
- idempotency
- retries
- verification after mutation
- auditability

### Exit criteria

Tsuzuri can safely modify one external account state through an explicit approval flow and verify the resulting external state.

---

## Phase 5 — Add the React + Vite UI

### Goal

Turn the working agent into an observable product without moving agent logic into the frontend.

### Frontend stack

- React
- TypeScript
- Vite

### Python service

Expose the agent through a small local HTTP API and stream execution events via SSE.

Use a custom event contract first. AG-UI or another standard agent-event protocol may be evaluated later, but it does not determine the visual UI and is not required for Phase 5.

### Initial UI areas

```text
Research
Chat
Agent Trace
Sources / Evidence
Approval
Settings
```

### Key UX principle

Do not hide agent execution behind a spinner.

Show useful runtime state:

```text
✓ Search VNDB
✓ Read Bangumi subject
● Searching web...

3 sources collected
1 conflicting claim detected
```

### What should be learned

- agent streaming UX
- SSE
- frontend/backend contract design
- explicit run state
- cancellation/error states
- approval UX
- displaying tool execution without exposing internal chain-of-thought

### Exit criteria

A user can complete the Phase 1–4 workflows entirely through the UI and understand what the agent is doing.

---

## Phase 6 — Local Structured Storage

### Goal

Stop rebuilding all personal context from remote APIs on every run.

### Preferred storage

SQLite for local-first desktop usage.

### Candidate entities

```text
Work
ExternalWorkMapping
UserWork
Progress
Rating
Playtime
Preference
AgentRun
ToolCall
Evidence
ActionRecord
```

### Core domain challenge

Entity resolution across Bangumi, VNDB, and Steam.

Example:

```text
Bangumi subject
VNDB v-id
Steam app-id
          ↓
      canonical Work
```

### What should be learned

- relational data modeling
- sync strategy
- source-of-truth decisions
- stale data
- conflict resolution
- entity resolution
- persisted action/audit state

### Exit criteria

Tsuzuri can maintain a stable local representation of the user's collection across multiple external sources.

---

## Phase 7 — RAG for Guides and Personal Knowledge

### Goal

Learn RAG only when there is a real need for unstructured local knowledge.

### Candidate knowledge

- walkthroughs
- wiki exports
- personal notes
- manuals
- long-form guides

### Possible flow

```text
Documents
  ↓
Parsing
  ↓
Chunking
  ↓
Embedding
  ↓
Local vector index
  ↓
search_knowledge tool
  ↓
Agent
```

### Strong use case

Spoiler-safe walkthrough assistance:

> I am in chapter 3. Tell me which CGs I can still miss without revealing anything after chapter 3.

### What should be learned

- chunking
- embeddings
- semantic retrieval
- metadata filtering
- hybrid retrieval
- context selection
- citations
- retrieval evaluation

### Exit criteria

RAG improves a measurable task that structured APIs and web search cannot solve reliably.

---

## Phase 8 — Evaluation and Observability

### Goal

Move from “it feels smart” to measurable agent quality.

### Build a small real-question dataset

Examples:

- identify the correct edition of a VN
- compare two releases
- determine adaptation coverage
- recommend from owned games under constraints
- update the correct collection item
- answer a spoiler-bounded guide question

### Metrics to explore

```text
Task success
Correct tool selection
Correct entity resolution
Source quality
Unsupported-claim rate
Write-action correctness
Latency
Token usage
Web-search cost
Human intervention
```

### Trace data

Record:

```text
AgentRun
ToolCall
Duration
Input/output size
Errors
Evidence
Approval
Final outcome
```

### What should be learned

- eval design
- regression testing for agents
- tracing
- cost/performance tradeoffs
- prompt/tool changes as testable engineering changes

### Exit criteria

A model, prompt, framework, or tool change can be evaluated against a repeatable test set instead of subjective impressions.

---

## Phase 9 — Optional Desktop Productization

### Goal

Package Tsuzuri as a local-first desktop application if the product has become useful enough to justify it.

### Candidate shell

Electron.

### Electron responsibilities

- application lifecycle
- system tray
- local file selection
- secure secret storage
- native notifications
- deep links/OAuth callbacks
- Python sidecar lifecycle
- updates

### Architecture

```text
Electron
├─ React + Vite renderer
└─ Python Agent sidecar
```

Electron should not absorb agent business logic.

### What should be learned

- desktop process boundaries
- IPC
- sidecar process management
- local packaging
- secret storage
- cross-platform distribution

### Exit criteria

Desktop packaging solves a real usability need rather than serving as architectural decoration.

---

# Deliberate exclusions during early learning

Do not add these until a concrete problem demands them:

- multi-agent systems
- Redis
- BullMQ/Celery-style queues
- microservices
- Kubernetes
- a separate vector database
- self-hosted LLM infrastructure
- full-site crawling
- durable workflow engines such as Temporal/DBOS/Prefect/Restate
- LangGraph before workflow complexity justifies an explicit graph
- automatic account writes without approval

The purpose of Tsuzuri is to understand agent engineering deeply, not to maximize the number of technologies in the architecture diagram.