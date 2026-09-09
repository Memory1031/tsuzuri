# Architecture

## 1. Architectural intent

Tsuzuri starts as an agent-learning project, not as a desktop application project.

The architecture therefore follows one rule:

> Keep the Agent Core independent from the UI and introduce infrastructure only when it teaches or enables a new capability.

The initial technical direction is:

- **Frontend:** React + TypeScript + Vite
- **Agent / Backend:** Python
- **Transport:** HTTP + SSE once the frontend is introduced
- **Desktop shell:** optional future Electron layer
- **Persistence:** local-first; SQLite is the default future choice

The UI should never become the place where agent orchestration logic lives.

---

## 2. Target architecture

```text
┌──────────────────────────────────────────────────────┐
│                     Tsuzuri                          │
│                                                      │
│  React + TypeScript + Vite                           │
│  ┌────────────────────────────────────────────────┐  │
│  │ Chat / Research / Library / Trace / Settings  │  │
│  └────────────────────────┬───────────────────────┘  │
│                           │                          │
│                      HTTP + SSE                      │
│                           │                          │
│  ┌────────────────────────▼───────────────────────┐  │
│  │              Python Agent Service             │  │
│  │                                                │  │
│  │ Agent Loop / State / Tool Registry            │  │
│  │ Research / Evidence / Memory / RAG            │  │
│  │ MCP Client / Approval / Evaluation            │  │
│  └───────────────┬────────────────────────────────┘  │
│                  │                                   │
│      ┌───────────┼──────────────┬─────────────┐      │
│      ▼           ▼              ▼             ▼      │
│   Bangumi      VNDB           Steam          Web     │
│   API/MCP      API/MCP        API            Search  │
│                                                      │
│                     SQLite                           │
└──────────────────────────────────────────────────────┘

Future optional layer:

Electron
  ├─ application lifecycle
  ├─ local file access
  ├─ secure token storage
  ├─ native notifications
  └─ Python sidecar lifecycle
```

Electron is intentionally outside the initial architecture. It is a product shell, not an agent requirement.

---

## 3. Responsibility boundaries

### 3.1 React + Vite

The frontend is responsible for presentation and interaction only.

Expected responsibilities:

- conversation UI
- research result presentation
- evidence/source display
- tool-call and trace visualization
- collection/library views
- action approval UI
- configuration UI

The frontend should not:

- directly call LLM providers
- own tool routing logic
- store provider secrets
- perform Bangumi/VNDB/Steam write actions directly
- implement RAG retrieval logic

### 3.2 Python Agent Core

Python owns the agent runtime and domain orchestration.

Core responsibilities:

- message/context lifecycle
- LLM calls
- tool registration and execution
- tool-call loop
- agent state
- source/evidence tracking
- write-action approval state
- MCP integration
- future RAG and memory
- evaluation hooks

The first implementation should prefer explicit code over agent frameworks so that the execution model stays understandable.

### 3.3 Tool layer

Tools should hide provider-specific details from the model.

Conceptual examples:

```text
search_bangumi(query)
get_bangumi_subject(subject_id)
search_vndb(query)
get_vndb_releases(vn_id)
web_search(query)
read_webpage(url)
```

Later personal/action tools may include:

```text
get_my_bangumi_collection()
update_bangumi_collection(...)
get_steam_library()
get_my_vndb_list()
update_vndb_list(...)
```

A tool should return structured, minimal data rather than dumping raw upstream responses into the context window.

---

## 4. Read vs. write capabilities

Tsuzuri should explicitly separate read tools from tools with side effects.

### Read tools

Safe to execute automatically in most cases:

- search a work
- retrieve metadata
- retrieve collection state
- retrieve Steam library/playtime
- perform web research
- search local knowledge

### Write tools

Require explicit approval by default:

- change collection status
- update episode/chapter progress
- submit a rating
- change VNDB list state
- post or modify user-generated content

Expected flow:

```text
User intent
   ↓
Agent prepares action
   ↓
Structured action preview
   ↓
Human approval
   ↓
Tool execution
   ↓
Audit result
```

This separation is a deliberate learning target: side effects, authorization, approval, retries, and auditability are core parts of production agent engineering.

---

## 5. Data-source strategy

Use structured sources first and live web research as a fallback or complement.

Preferred order:

```text
Structured API / MCP
        ↓
Can it answer the question?
   ├─ yes → use it
   └─ no  → Web Search / Web Fetch
```

Examples:

- VNDB rating → VNDB API
- Bangumi collection state → Bangumi API/MCP
- Steam playtime → Steam API
- newly announced teaser → Web Search
- Steam edition censorship/version differences → VNDB + Web research

This reduces cost, improves factual consistency, and keeps provenance clearer.

---

## 6. Data model direction

Structured personal data should not be treated as RAG content.

Future local structured entities may include:

```text
Work
ExternalWorkMapping
UserWork
Progress
Rating
Playtime
AgentRun
ToolCall
Evidence
Preference
```

A work may map to multiple external systems:

```text
Work
├─ Bangumi subject_id
├─ VNDB vn_id
└─ Steam app_id
```

Cross-source mapping is an important future domain problem because titles and editions are not always one-to-one.

### Entity resolution examples

Likely same work:

```text
STEINS;GATE
シュタインズ・ゲート
命运石之门
```

Must not be merged automatically:

```text
STEINS;GATE
STEINS;GATE ELITE
STEINS;GATE RE:BOOT
```

Entity resolution should eventually become an explicit service/module rather than implicit LLM guesswork.

---

## 7. RAG boundary

RAG is not part of the initial implementation.

RAG should be introduced for unstructured knowledge such as:

- walkthroughs
- wiki articles
- personal notes
- guides
- long reviews
- manuals

Structured data such as status, rating, playtime, IDs, and collection state belongs in ordinary storage and tool results.

Future RAG flow:

```text
Document
  ↓
Chunk
  ↓
Embedding
  ↓
Vector index
  ↓
Retrieval
  ↓
Agent context
```

RAG should appear only after the team can explain exactly how ordinary tool calling works.

---

## 8. Communication model

When the frontend is introduced, the Python agent service should expose a small local API.

Suggested pattern:

```text
POST /runs
GET  /runs/{run_id}
GET  /runs/{run_id}/events   (SSE)
POST /runs/{run_id}/approve
```

SSE events can model the runtime explicitly:

```text
run.started
tool.started
tool.completed
evidence.added
message.delta
approval.required
run.completed
run.failed
```

This enables the UI to display agent execution rather than hiding everything behind a loading spinner.

---

## 9. Repository evolution

Do not create the final directory tree before each layer exists.

A reasonable end-state may become:

```text
tsuzuri/
├─ web/                 # React + TypeScript + Vite
│  └─ src/
├─ agent/               # Python Agent Core
│  ├─ core/
│  ├─ tools/
│  ├─ integrations/
│  ├─ research/
│  ├─ memory/
│  └─ rag/
├─ docs/
├─ scripts/
└─ README.md
```

If Electron is introduced later:

```text
├─ desktop/             # Electron shell only
```

The directory should follow implemented responsibilities rather than anticipate them.

---

## 10. Architectural non-goals for the early project

Avoid adding these merely because they are common in AI architecture diagrams:

- multi-agent orchestration
- Redis
- task queues
- microservices
- Kubernetes
- a dedicated vector database
- local model hosting
- a complete VNDB mirror
- custom web crawler/search engine
- autonomous account mutations without approval

Each new component must answer one question:

> What new capability or learning objective does this component enable right now?
