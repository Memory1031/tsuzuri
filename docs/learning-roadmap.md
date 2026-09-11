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
P2  Google ADK Comparison          ← current
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

You can explain the full execution lifecycle without relying on LangGraph/ADK terminology.

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

## Phase 2 — Introduce a Framework Deliberately ← Current

### Goal

Learn what an agent framework actually solves after understanding the raw loop.

### Selected framework

**Google ADK**

The raw P0/P1 implementation has now exposed enough runtime concerns—state, tool execution, events, streaming, termination, and research memory—that a framework comparison is useful rather than premature.

### Migration target

Rebuild the Phase 1 workflow with Google ADK while preserving behavior as much as practical.

### What should be compared

```text
Raw implementation
vs.
Google ADK implementation
```

Compare:

- state representation
- Runner / execution lifecycle
- Session / State / Event concepts
- tool registration
- retries and error propagation
- checkpoints / persistence boundaries
- interruptions
- streaming
- callbacks
- tracing
- testability
- code complexity

### What should be learned

The objective is not “learn ADK syntax”.

The objective is to answer:

> Which generic runtime problems does ADK remove, and which Tsuzuri-specific problems remain ours?

In particular, Evidence semantics, ACGN entity resolution, source-quality policy, and future collection/action rules remain product/domain concerns even if runtime orchestration moves into a framework.

### Exit criteria

You can justify why ADK stays in the project, which raw-runtime code it replaces, and which abstractions Tsuzuri still needs to own.

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

## Phase 4 — MCP and Action Tools

### Goal

Learn MCP and safe side effects through a real workflow.

### Strategy

- reuse an existing Bangumi MCP implementation when practical
- implement a small VNDB MCP server yourself for learning

### Example VNDB MCP tools

```text
search_vn
get_vn
get_releases
get_my_list
update_my_list
```

### Write capabilities

Later allow actions such as:

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

Tsuzuri can safely modify one external account state through an explicit approval flow.

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
- automatic account writes without approval

The purpose of Tsuzuri is to understand agent engineering deeply, not to maximize the number of technologies in the architecture diagram.
