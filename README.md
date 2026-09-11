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

## Current status

**Phase 0 — Understanding the Agent Loop: complete.**

The raw implementation covers tool schemas, tool selection, tool dispatch, multiple tool calls, result propagation, recoverable error observations, bounded execution, and explicit message state without relying on an agent framework.

The Phase 0 implementation is preserved at [`agent/examples/phase0_agent_loop.py`](agent/examples/phase0_agent_loop.py).

**Phase 1 — Research + Evidence: complete.**

The Phase 1 raw runtime adds real VNDB integration, web search and webpage reading, grounding policy, research termination, runtime-owned source references, evidence collection, and evidence-only final synthesis.

Two references are preserved:

- [`agent/examples/phase1_research_agent.py`](agent/examples/phase1_research_agent.py) — the intentionally smaller Phase 1 baseline.
- [`agent/examples/phase1_citation_transport.py`](agent/examples/phase1_citation_transport.py) — the later streaming citation transport experiment, preserved as an exploration rather than the baseline.

The current milestone is **Phase 2 — Agent Framework Comparison**. Tsuzuri will rebuild the Phase 1 workflow with **PydanticAI**, then validate an approval-gated mock write action before real account mutations are introduced.

The framework choice is deliberate: PydanticAI matches Tsuzuri's local-first, Pythonic, provider-neutral direction without requiring an explicit workflow graph or a cloud-platform-centered runtime. LangGraph remains a future option if workflow orchestration becomes genuinely complex; Google ADK remains useful to study separately for Google-oriented production systems.