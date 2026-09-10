# Phase 0 — Understanding the Agent Loop

## Goal

理解一个最小 Agent 是如何从普通 LLM Call 演变而来的。

## 1. From LLM Call to Agent

普通 LLM：

User
→ Model
→ Text

Agent：

User
→ Model
→ Action
→ Tool
→ Observation
→ Model
→ Final Answer