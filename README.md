# Tsuzuri（綴り）

> A personal ACGN research and collection agent for anime, games, visual novels, and beyond.

Tsuzuri is an experimental personal agent project focused on learning and applying practical agent engineering through real ACGN use cases.

The project will evolve from a minimal tool-calling research agent into a personal assistant that can understand collection history, research works across multiple sources, manage authorized collection actions, and eventually use local knowledge and long-term context.

## Project goals

- Learn agent fundamentals by building the core workflow before introducing heavy frameworks.
- Create something useful for real ACGN research rather than a tutorial-only chatbot.
- Combine structured sources such as Bangumi, VNDB, and Steam with live web research.
- Keep read operations, write actions, personal data, and local knowledge clearly separated.
- Grow toward a local-first personal agent while keeping the architecture simple enough to understand.

## Planned stack

- **Frontend:** React + TypeScript + Vite
- **Agent / Backend:** Python + PydanticAI
- **Communication:** HTTP + SSE when the UI is introduced
- **External tools:** Bangumi, VNDB, Steam, Web Search, MCP
- **Persistence:** introduced later; SQLite is preferred for local-first usage
- **RAG:** introduced only after the basic agent and tool-calling workflow is understood
- **Desktop shell:** optional future layer, likely Electron; not part of the initial implementation

## Documentation

- [Architecture](docs/architecture.md)
- [Learning & Evolution Roadmap](docs/learning-roadmap.md)
- [Learning Notes](docs/learning/README.md)
- [Phase 0 — Understanding the Agent Loop](docs/learning/phase-0-agent-loop.md)
- [Phase 1 — Research, Evidence, and Research Memory](docs/learning/phase-1-research-evidence.md)
- [Phase 2 — Framework Selection](docs/learning/phase-2-framework-selection.md)
- [Phase 2.2 — Runtime-Guaranteed Evidence Pipeline](docs/learning/phase-2-2-evidence-pipeline.md)

## Current status

**Phase 0 — Understanding the Agent Loop: complete.**

The raw implementation covers tool schemas, tool selection, tool dispatch, multiple tool calls, result propagation, recoverable error observations, bounded execution, and explicit message state without relying on an agent framework.

The Phase 0 implementation is preserved at [`agent/examples/phase0_agent_loop.py`](agent/examples/phase0_agent_loop.py).

**Phase 1 — Research + Evidence: complete.**

The Phase 1 raw runtime adds real VNDB integration, web search and webpage reading, grounding policy, research termination, runtime-owned source references, evidence collection, and evidence-only final synthesis.

Two references are preserved:

- [`agent/examples/phase1_research_agent.py`](agent/examples/phase1_research_agent.py) — the intentionally smaller Phase 1 baseline.
- [`agent/examples/phase1_citation_transport.py`](agent/examples/phase1_citation_transport.py) — the later streaming citation transport experiment, preserved as an exploration rather than the baseline.

**Phase 2.1 — Raw P1 → PydanticAI: complete.**

The Phase 1 research toolset now runs on PydanticAI, delegating generic tool schema/dispatch, typed output, provider integration, RunContext injection, and usage-limit mechanics to the SDK while keeping Tsuzuri-specific research semantics explicit.

**Phase 2.2 — Evidence Pipeline: complete.**

P2.2 replaced model-dependent `save_evidence` as the preferred research baseline with runtime-guaranteed Observation/Source capture followed by semantic, source-isolated Evidence extraction. The experiment also explored Evidence consolidation, conflict/sufficiency analysis, bounded research rounds, transport budgets, concurrent extraction, graceful degradation, evidence-only final synthesis, and FAST / GROUNDED / DEEP research modes.

The complete learning summary is in [`docs/learning/phase-2-2-evidence-pipeline.md`](docs/learning/phase-2-2-evidence-pipeline.md).

The P2.2 implementation intentionally expanded into a large laboratory pipeline. That code is evidence about the problem space, not the target long-term architecture. Before adding more orchestration, the discovered contracts should be compressed into a smaller readable research baseline.

The current milestone is **P2.3 — Planning**: test explicit planning only on research tasks complex enough that decomposition may improve quality. Planning should remain opt-in rather than becoming mandatory ceremony for every question.

PydanticAI remains the primary SDK. LangGraph remains a future option if, after compression, Tsuzuri's real workflow is still clearer as an explicit graph/state machine rather than ordinary Python orchestration.