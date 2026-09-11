# Phase 1 — Research, Evidence, and Research Memory

Phase 1 moves Tsuzuri from a minimal tool-calling loop to a small research agent that can investigate a question, compare sources, preserve useful evidence, and synthesize an answer from the evidence it chose to keep.

The purpose of this phase was not to build a production web research framework. It was to understand the mechanics that appear once an agent must do more than call one API and return one answer.

---

## 1. Problem

A simple tool-calling agent can do this:

```text
User
  ↓
Model
  ↓
Tool Call
  ↓
Tool Result
  ↓
Model
  ↓
Answer
```

That is not yet research.

A research question often requires:

```text
Question
  ↓
Find candidate sources
  ↓
Resolve the right entity
  ↓
Read additional detail
  ↓
Compare conflicting evidence
  ↓
Decide when evidence is sufficient
  ↓
Preserve important findings
  ↓
Answer with uncertainty when needed
```

Phase 1 therefore focused on five new problems:

1. How should structured APIs and web research work together?
2. How does the agent distinguish discovery from verification?
3. How should the runtime represent provenance and evidence?
4. How should research stop instead of searching forever?
5. How can research survive when raw working context is discarded?

---

## 2. Real structured data: VNDB Kana API

Phase 0 used mock tools. Phase 1 replaced the mock VNDB path with the real VNDB Kana API.

The first useful distinction was:

```text
search_vndb
    ↓
Candidate discovery

get_vndb
    ↓
Exact entity detail
```

For example:

```text
search_vndb("STEINS;GATE")
    ↓
v2002 STEINS;GATE
v17102 STEINS;GATE 0
...

get_vndb("v2002")
    ↓
exact detail for the selected work
```

This is a reusable pattern:

```text
Search → Resolve → Get
```

Search should usually return a light candidate set. Get can return richer data after the target entity is known. This avoids pushing large provider responses into context unnecessarily.

### Boundary validation with Pydantic

Real external APIs introduced validation problems that mocks hid.

A typo such as `fileds` or `alttile` caused an HTTP 400, and trying to immediately parse the body as JSON produced a second error. The useful separation is:

```text
HTTP status
≠
response body
≠
validated domain data
```

The implementation therefore uses:

```text
httpx response
  ↓
raise_for_status()
  ↓
response.json()
  ↓
Pydantic model_validate(...)
  ↓
Tsuzuri domain object
```

Pydantic is useful at the external boundary because it turns provider payloads into explicit typed objects and exposes malformed data early.

### Provider model vs. Tsuzuri model

A provider response should not automatically become the model-facing tool contract.

For web search, Tavily calls the search excerpt `content`, but Tsuzuri exposes it as `snippet` because that is the more accurate domain meaning.

This creates an adapter boundary:

```text
Provider response
      ↓
Provider-specific model
      ↓ normalize
Tsuzuri model
      ↓
Agent
```

The agent should express semantic intent, not operate every provider-specific option.

---

## 3. Grounding exposed semantic failures

A tool can succeed technically and still fail the task semantically.

During an early test, the temporary Bangumi mock always returned the original STEINS;GATE regardless of query. The model searched for STEINS;GATE 0, received a valid-looking mock response for the wrong work, retried several language variants, and eventually chose the correct VNDB entity while reporting that Bangumi had not verified the target.

This exposed three different failure classes:

```text
1. Runtime / model-output failure
   invalid JSON, invalid arguments

2. External-system failure
   timeout, HTTP error, rate limit, 403

3. Semantic failure
   wrong entity, ambiguous result, insufficient evidence
```

The third class is especially important for agents because an HTTP 200 does not mean the user's intent was satisfied.

### Grounding policy

The research prompt was tightened so factual claims about works, dates, ratings, releases, and developers should use tool results as the source of truth.

This improved behavior, but also exposed an important limitation:

> Tool calling does not automatically guarantee grounded final answers.

The model can still mix tool evidence, background knowledge, and inference. Grounding therefore needs both policy and provenance, not only a prompt sentence.

---

## 4. Web research: Search is not Read

Phase 1 added two different web capabilities:

```text
web_search(query)
    ↓
Discovery

read_webpage(url)
    ↓
Retrieval / verification
```

This is intentionally similar to:

```text
search_vndb → get_vndb
```

A search tool should return a small candidate list such as:

```text
title
url
snippet
relevance_score
```

A webpage reader can then retrieve the selected page.

The separation matters for context cost. Searching ten results is cheap compared with placing ten full webpages in the model context.

### Relevance is not authority

Tavily returns a relevance score. It does not mean source credibility.

For a query about the Steam release date, Wikipedia may have a higher search relevance score than the official Steam page. That does not make Wikipedia a stronger authority for the Steam store release date.

This distinction will eventually require source-quality policy, but Phase 1 intentionally did not create a large scoring system prematurely.

---

## 5. Web retrieval is infrastructure, not a trivial GET

The first `read_webpage` implementation used `httpx` plus `trafilatura`.

Real tests quickly exposed why mature agents often delegate web retrieval to dedicated infrastructure:

- Steam contained the release date visually, but the text extractor omitted that field.
- wiki.gg returned 403.
- SteamDB returned 403.
- Steam's JSON appdetails endpoint was not compatible with an HTML-only reader.
- webpages may require JavaScript, cookies, age gates, authentication, or anti-bot handling.

Therefore:

```text
page contains fact
≠
reader successfully extracts fact
```

A production reader may need multiple backends:

```text
read_webpage
   ↓
local HTTP + extractor
   ↓ fallback
provider extraction API / browser
```

Phase 1 keeps the local implementation because it makes the mechanics visible. It is not intended to become a full crawling stack.

---

## 6. Research termination

The first bounded research loop used:

```python
MAX_STEPS = 5
```

A real trace reached step 5, performed another tool call, and then raised an error before the model had one final chance to synthesize the collected results.

This exposed three different kinds of termination:

```text
Model termination
→ the model decides it is done

Policy termination
→ research rules say evidence is sufficient

Runtime termination
→ hard budget is exhausted
```

The runtime budget is necessary even when the prompt tells the model not to search indefinitely.

A useful principle emerged:

> If a constraint can be enforced by the runtime, do not rely only on the prompt.

### Final synthesis after research

Instead of crashing after the research budget is exhausted, the runtime should enter a synthesis phase where tools are no longer available.

Conceptually:

```text
Research Phase
Model ↔ Tools
     ↓
Research ends
     ↓
Synthesis Phase
No tools
     ↓
Final answer
```

This is the first point where the raw loop begins to resemble a state machine rather than a simple `while True`.

---

## 7. State is not Context

In Phase 0, the simplest mental model was:

```text
Agent State ≈ messages
```

Phase 1 outgrew that model.

Research now has multiple categories of state:

```text
messages
→ working model context

tool observations
→ raw runtime observations

evidence sources
→ addressable source choices

evidence store
→ selected research memory
```

This leads to a more useful distinction:

> State is everything the runtime knows. Context is the subset the runtime chooses to send to the model for the current step.

A context builder is therefore a projection:

```text
Agent State
    ↓
Context Builder
    ↓
LLM Context
```

This concept becomes important for compaction, long-running sessions, and framework-managed state later.

---

## 8. Evidence as research memory

The first Evidence model separates the model's interpretation from the source material:

```text
claim
→ what the research agent believes the evidence supports

support
→ source material preserved by the runtime
```

This distinction proved valuable. In one test, the research agent incorrectly interpreted a Wikipedia release table. Final synthesis could detect the mismatch because the raw support text was still preserved.

That gives a useful safety property:

```text
Research interpretation can be wrong
but
source material remains available for later correction
```

### Evidence is lossy memory

Evidence is not a lossless replacement for raw documents.

If a webpage contains A, B, C, and D, but the agent preserves only A and B, later context rebuilding cannot recover C unless the raw document was stored elsewhere.

A mature architecture may therefore eventually separate:

```text
Working Context
Evidence Memory
Raw Artifacts / Documents
```

Phase 1 implements only the first two ideas and leaves artifact storage for later.

---

## 9. Do not ask the model to regenerate runtime facts

The first `save_evidence` design asked the model to provide fields such as:

```text
source_tool_call_id
source_name
source_url
support
claim
```

This was fragile.

The model invented aliases such as `web_search-1` instead of copying opaque provider tool-call IDs. Exact support copying also failed because LLMs are poor at character-perfect reproduction and serialized JSON escaping changed line breaks.

This produced one of the strongest design lessons in Phase 1:

> Let the LLM choose. Let the runtime fill information it already knows.

### ID layers

Three different identities appeared:

```text
Provider protocol ID
call_-727...
    ↓
Runtime observation ID
obs_2
    ↓
Addressable source ref
obs_2:r2
    ↓
Durable evidence ID
ev_1
```

The provider's `tool_call_id` exists for protocol correlation. It is not a good business-level identifier for the model.

The runtime therefore creates stable, simpler source refs and exposes them as choices.

For a web search result:

```text
obs_2:r1 → Wikipedia result
obs_2:r2 → Steam result
obs_2:r3 → another result
```

The agent only needs to do:

```text
save_evidence(
  source_ref="obs_2:r2",
  claim="Steam lists the release date as ..."
)
```

The runtime fills:

```text
source name
URL
support text
kind
observation identity
```

This converts a fragile fill-in-the-blank task into a smaller selection task.

---

## 10. Evidence-only final synthesis

The final Phase 1 baseline separates research from answer generation.

Research uses the full working context and tools:

```text
Original Question
      ↓
Model ↔ Tools
      ↓
save_evidence
      ↓
Evidence Store
```

Final synthesis intentionally does not reuse the entire research transcript:

```text
Original Question
+
Evidence Store
      ↓
Final Model
```

Old searches, failed requests, raw webpages, and intermediate tool chatter are omitted.

This validates an important idea:

> Research Memory can preserve selected findings after the working context is discarded.

It also makes missing evidence visible. If the final model cannot state a fact because it was never saved, the problem belongs to research-memory selection rather than hidden raw context.

---

## 11. Citation transport experiment

Phase 1 briefly explored how internal Evidence IDs might become user-facing citations in a streaming UI.

The experiment introduced:

```text
Evidence ID
   ↓
response-scoped opaque citation token
   ↓
stream parser
   ↓
content_delta / citation event
   ↓
public citation number
```

This solved several real concerns:

- internal `ev_1` identifiers should not be shown to users;
- normal text such as `[S1]` should not accidentally be interpreted as protocol;
- streaming can split control tokens across chunks;
- invalid citation tokens should never leak into user-visible content;
- public citation numbering should be independent from Evidence Store ordering.

However, continuing this work would require increasingly generic infrastructure:

- streaming event protocols;
- parser state;
- citation metadata transport;
- UI rendering contracts;
- source normalization across every tool;
- session and persistence behavior.

At that point the project was starting to rebuild an agent framework and presentation transport layer rather than learn a new domain capability.

The experiment is therefore preserved at:

```text
agent/examples/phase1_citation_transport.py
```

It is intentionally not the Phase 1 baseline.

---

## 12. Phase 1 baseline

The smaller reference implementation is:

```text
agent/examples/phase1_research_agent.py
```

Its core flow is:

```text
Question
  ↓
Research loop
  ↓
Structured API / web tools
  ↓
Runtime observations
  ↓
Runtime-owned source refs
  ↓
Model selects source_ref
  ↓
save_evidence
  ↓
Evidence Store
  ↓
Evidence-only final synthesis
```

This is the point at which the raw implementation stops deliberately.

---

## 13. Current limitations

The Phase 1 implementation is intentionally incomplete as production infrastructure.

Known limitations include:

- only `web_search` results currently receive normalized addressable `source_ref` values;
- VNDB and webpage observations are not yet normalized into the same evidence-source interface;
- source authority is handled by prompt policy rather than a formal ranking model;
- webpage reading is a basic HTTP + extraction implementation;
- Evidence is in memory only;
- no persistent sessions or checkpoints;
- no formal token-budget compaction policy;
- no evaluation suite;
- no write actions or approval flow;
- no production citation transport/UI.

These are useful limitations because they now create a concrete basis for evaluating an agent framework.

---

## 14. Why introduce a framework now?

Before Phase 0 and Phase 1, abstractions such as these were only framework vocabulary:

```text
Runner
Session
State
Event
Tool
Callback
Streaming
Checkpoint
```

After implementing the raw research agent, the problems behind those abstractions are visible.

The next phase should therefore rebuild the same behavior with Google ADK and compare:

```text
Raw implementation
vs.
ADK implementation
```

Questions for Phase 2 include:

- What replaces the explicit message loop?
- How does ADK represent session and state?
- How are tools registered and tool results represented?
- How are limits, callbacks, retries, and interruptions handled?
- What does ADK provide for streaming and events?
- Where should Tsuzuri-specific Evidence semantics live?
- Which parts remain product/domain responsibility even after adopting a framework?

The purpose of Phase 2 is not to learn framework syntax. It is to identify which generic runtime responsibilities can now be delegated without losing understanding of how they work.

---

## 15. Exit questions

Phase 1 is complete when these can be explained without relying on framework terminology:

1. Why are Search and Get/Read separate operations?
2. Why is search relevance not the same as source authority?
3. What is the difference between a technical tool failure and a semantic failure?
4. Why can a webpage contain a fact that the reader fails to extract?
5. Why does a research agent need both soft stopping policy and hard runtime budgets?
6. What is the difference between Agent State and model Context?
7. Why are raw observations different from Evidence?
8. Why should Evidence preserve both `claim` and source `support`?
9. Why should the model select a runtime-owned source ref instead of regenerating URLs, IDs, and source text?
10. Why can Evidence Memory support final synthesis after raw research context is removed?
11. Which parts of the current implementation are generic agent infrastructure that should now be compared with ADK?

If these answers are clear, the raw implementation has done its job.
