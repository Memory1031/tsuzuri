# Learning Notes

Tsuzuri is also a practical learning project for agent engineering.

The notes in this directory are organized by roadmap phase. They focus on the underlying mechanism and the reasoning behind each abstraction rather than serving as API reference material.

Each phase should answer five questions:

1. What problem are we solving?
2. How does it work underneath?
3. What did we implement ourselves?
4. What failure modes or limitations did we observe?
5. Why do we need the next abstraction?

## Phases

- [Phase 0 — Understanding the Agent Loop](phase-0-agent-loop.md) ✅
- [Phase 1 — Research, Evidence, and Research Memory](phase-1-research-evidence.md) ✅
- [Phase 2 — Framework Selection](phase-2-framework-selection.md)
- [Phase 2.2 — Runtime-Guaranteed Evidence Pipeline](phase-2-2-evidence-pipeline.md) ✅

## Current

**P2.3 — Planning** is next.

P2.2 validated runtime-guaranteed source capture, source-specific Evidence extraction, provenance, conflict/sufficiency analysis, bounded research rounds, graceful degradation, evidence-only synthesis, and the FAST / GROUNDED / DEEP capability ladder.

The main lesson is not that every research request should run the complete DEEP pipeline. The P2.2 implementation intentionally expanded until the orchestration itself became difficult to read. Before adding Planning, the discovered semantic contracts should be compressed into a smaller research baseline.

Planning will then be tested only on research tasks complex enough that explicit decomposition may improve quality. It should remain optional rather than becoming mandatory ceremony for every question.

PydanticAI remains Tsuzuri's primary SDK. LangGraph remains a future orchestration option only if the compressed real application flow still becomes easier to understand as an explicit graph/state machine.