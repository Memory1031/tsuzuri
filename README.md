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
- **Agent / Backend:** Python
- **Communication:** HTTP + SSE when the UI is introduced
- **External tools:** Bangumi, VNDB, Steam, Web Search, MCP
- **Persistence:** introduced later; SQLite is preferred for local-first usage
- **RAG:** introduced only after the basic agent and tool-calling workflow is understood
- **Desktop shell:** optional future layer, likely Electron; not part of the initial implementation

## Documentation

- [Architecture](docs/architecture.md)
- [Learning & Evolution Roadmap](docs/learning-roadmap.md)

## Current status

Architecture and learning plan only. Implementation has not started yet.
