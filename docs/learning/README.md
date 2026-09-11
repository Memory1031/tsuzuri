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
- [Phase 2 — Framework Selection](phase-2-framework-selection.md) ← current

## Next

Phase 2 will rebuild the Phase 1 workflow with **PydanticAI** and compare the SDK-based implementation against the raw runtime.

The goal is not to learn framework syntax in isolation. The comparison should make clear which generic runtime responsibilities PydanticAI removes, which Tsuzuri-specific responsibilities remain ours, and whether the same SDK can comfortably support both research and an approval-gated mock write action.

LangGraph remains a future orchestration option if explicit long-lived workflow/state-machine complexity appears. Google ADK remains useful to study separately for Google Cloud / Vertex-oriented agent systems rather than being the default Tsuzuri runtime.