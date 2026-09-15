# Phase 2.2 — Runtime-Guaranteed Evidence Pipeline

Phase 2.2 asks a narrower question than “how do we build a deep research agent?”:

> After moving the Phase 1 research loop onto PydanticAI, which parts of Evidence collection should be guaranteed by the runtime, and which parts should remain semantic model decisions?

The experiment started from the Phase 1 design:

```text
Tool Result
  ↓
Research Agent decides it matters
  ↓
save_evidence(source_ref, claim)
  ↓
Evidence Store
```

That design made the Evidence boundary explicit, but it had an obvious recall failure: an important source could be retrieved correctly and still disappear from the final grounded answer if the model forgot to call `save_evidence`.

P2.2 therefore explored a runtime-guaranteed alternative and deliberately pushed it far enough to expose its reliability, cost, latency, orchestration, and maintainability trade-offs.

---

## Final direction

The selected Evidence model is:

```text
Tool Result
  ↓
Runtime automatically captures Observation
  ↓
Runtime normalizes SourceCandidate
  ↓
semantic source selection / extraction
  ↓
Runtime validates support + fills provenance
  ↓
Evidence
```

The key boundary is:

> **LLM chooses semantic meaning; Runtime fills and validates mechanical facts it already knows.**

The runtime does **not** “understand evidence” by itself. It guarantees provenance, IDs, source ownership, execution boundaries, and mechanical validation. The model still decides relevance, claim meaning, equivalence, conflict, sufficiency, and final wording.

This means the Phase 1 model-facing `save_evidence(...)` tool is no longer the preferred baseline for Tsuzuri research. It remains useful as a learning reference, but normal retrieval should create a durable source trail automatically.

---

## 1. State is not Context

P2.2 first made the distinction between runtime state and model context concrete through PydanticAI `deps` / `RunContext`.

```text
Model Context
- instructions
- messages
- tool results
- prompt tokens

Runtime State
- observations
- source candidates
- evidence
- counters / IDs
- processing status
```

`ResearchState` is injected by the runtime and is invisible to the model unless Tsuzuri deliberately serializes some part of it into a prompt.

This became the basis for the whole pipeline: model-visible context can remain task-specific while provenance and durable research state live outside the transcript.

---

## 2. Capture every retrieval attempt as an Observation

All retrieval tools were wrapped so successful and expected-failure attempts are recorded mechanically:

```text
search_vndb
get_vndb
web_search
read_webpage
```

An Observation records facts the runtime already knows:

```text
obs_N
- tool name
- arguments
- success / failure
- result or error
```

This exposed an important distinction from framework usage counters. In observed runs, failed `ToolFailed` retrieval attempts could appear in Tsuzuri's Observation history even when PydanticAI's successful `tool_calls` usage count did not increase.

Therefore:

```text
PydanticAI Usage
≠
complete research attempt history
```

The framework usage object remains useful for budgets and cost; Tsuzuri state remains the authoritative research trace.

---

## 3. Normalize retrieval output into SourceCandidate

One Observation may produce zero, one, or many candidate sources:

```text
web_search
  → 1 Observation
  → N WEB_SEARCH_SNIPPET candidates

search_vndb
  → 1 Observation
  → N STRUCTURED_API candidates

get_vndb
  → 1 Observation
  → 1 STRUCTURED_API candidate

read_webpage
  → 1 Observation
  → 1 WEB_PAGE candidate

failed retrieval
  → Observation only
```

The resulting provenance chain begins as:

```text
obs_N → src_N
```

`SourceCandidate.content` intentionally remains typed where possible. Structured VNDB objects do not need to be flattened into strings until an LLM or storage boundary requires serialization.

Candidate duplication is also allowed. The same VNDB work or URL may be discovered by more than one retrieval step because a candidate represents **how this run obtained a source**, not a globally deduplicated entity.

---

## 4. Evidence is source-specific semantic support

Evidence extraction introduced a new durable layer:

```text
ev_N
- source_candidate_id
- observation_id
- claim
- support
```

The model chooses:

```text
claim
support
```

The runtime fills:

```text
ev_N
source_candidate_id
observation_id
```

This avoids asking the model to regenerate provenance the runtime already owns.

The support validator mechanically checks that the quoted support actually exists in the selected source after whitespace normalization.

This gives two different grounding levels:

```text
Quote grounding
support ∈ source
→ mechanically verifiable

Semantic grounding
support ⊨ claim
→ still a semantic judgment
```

The second problem is not magically solved by substring validation.

---

## 5. Batch extraction exposed cross-source semantic leakage

The first optimized extractor accepted several sources in one model request and asked the model to choose `source_candidate_id`, `claim`, and `support`.

It improved throughput, but the experiment exposed a subtle failure: a claim attached to source A could include context learned from source B even when its quoted support came only from A.

Examples included claims that combined VNDB data with a Steam release fact learned from another source.

The fix was architectural rather than prompt-only:

```text
multiple SourceCandidates
  ↓
Source Selector
  ↓
selected src_N
  ↓
Single-source Evidence Extractor
```

A single-source extractor physically cannot see another source, so provenance leakage becomes much harder.

This led to a general lesson:

> Components that establish **single-source provenance** should ideally see one source. Components that perform **cross-source reasoning** may see multiple validated facts.

Once the extractor receives exactly one source, `source_candidate_id` disappears from model output entirely. The runtime already knows which source is being processed.

---

## 6. Evidence extraction became incremental and concurrent

A source can be processed and produce Evidence, or be processed and produce no useful Evidence.

Therefore “has Evidence” is not equivalent to “has already been processed”. `ResearchState` gained explicit processed-source tracking so irrelevant candidates are not repeatedly sent to the extractor.

```text
SourceCandidate
├─ unprocessed
├─ processed → Evidence
└─ processed → no relevant Evidence
```

Pending sources are handled in batches for source selection, while selected single-source extraction calls can run concurrently with a bounded semaphore.

The preferred state-write pattern is:

```text
concurrent work
→ return results
→ runtime commits Evidence in deterministic order
```

rather than allowing completion order to determine `ev_N` numbering.

Concurrency itself is another runtime budget:

```text
request budget
latency budget
retry budget
concurrency budget
```

---

## 7. Consolidation is not another free-form summarization step

Once source-specific Evidence existed, P2.2 added an optional cross-source layer:

```text
Evidence
  ↓
Evidence groups
  ↓
Findings
  ↓
Conflicts
```

The first consolidator generated new consolidated claim text. This immediately produced an important failure: conflicting `8 Sep` and `9 Sep` release-date Evidence was “explained” as a regional/time-zone difference even though the supplied Evidence did not establish that explanation.

The consolidator was therefore restricted to grouping IDs and choosing a representative existing Evidence item.

Runtime then creates the Finding claim from that representative Evidence rather than accepting newly invented factual text.

Another invariant was added:

> Every Evidence record in a group must independently support the full proposition represented by the group's claim.

For example, Evidence supporting only `9 Sep 2016` must not be grouped under a more specific Finding that says `9 Sep 2016 at 20:00 UTC` unless it independently supports the time too.

This preserves the distinction between:

```text
Evidence
= source-specific support record

Finding
= semantic proposition supported by one or more Evidence records

Conflict
= relation between Findings
```

The runtime also validates that Evidence grouping is a real partition: known IDs only, no duplicate membership, no missing Evidence, valid conflict references.

---

## 8. Sufficiency is question-relative, not “search until budget ends”

Research termination initially depended mostly on model behavior plus `tool_calls_limit`. Repeated runs showed the model could continue searching even after useful sources were already available and eventually hit the hard tool limit.

P2.2 added an explicit semantic assessment:

```text
SUFFICIENT
SUFFICIENT_WITH_CONFLICT
NEEDS_MORE_RESEARCH
```

A conflict does not automatically require more research.

For example:

```text
Question: Was the Steam launch in September 2016?
8 Sep vs 9 Sep
→ sufficient_with_conflict may be fine
```

while:

```text
Question: Confirm the exact launch day.
8 Sep vs 9 Sep
→ further research may be justified
```

If more research is required, the assessor must identify a specific non-overlapping research gap rather than saying “find more sources”.

This enabled gap-directed continuation:

```text
research round
→ Evidence
→ analysis
→ specific gap
→ bounded gap research
→ only process new sources
→ reassess
```

---

## 9. Hard budgets are round boundaries, not necessarily fatal errors

A major runtime lesson came from PydanticAI usage limits.

The model may issue a batch of tool calls whose projected total exceeds the limit. PydanticAI correctly blocks the batch before execution and raises `UsageLimitExceeded`.

At first this aborted the entire research run.

For a bounded research round, however, reaching the tool budget is often an intentional control boundary:

```text
budget exhausted
≠
whole task failed
```

The runtime now treats research-round budget exhaustion as a stop reason, keeps all already captured Observations/Sources, and continues into Evidence processing.

This yields two complementary stopping mechanisms:

```text
semantic stop
→ research state says enough

hard stop
→ runtime budget forces control back
```

`tool_calls_limit` becomes a safety / round boundary rather than the primary research policy.

---

## 10. Timeout, retry, and concurrency are different budgets

A particularly useful failure made the transport layers visible.

A consolidator configured with:

```text
ModelSettings(timeout=60)
```

was observed to fail after roughly 181 seconds.

The reason was retry multiplication:

```text
PydanticAI model request
  ↓
OpenAI-compatible client attempt #1 → 60s
attempt #2 → 60s
attempt #3 → 60s
```

The provider SDK's hidden retries were independent of PydanticAI request budgets.

P2.2 therefore made retry behavior explicit by injecting an `AsyncOpenAI` client only to set:

```text
max_retries = 0
```

while keeping PydanticAI responsible for the agent/model abstraction and using model settings for attempt timeout.

The resulting mental model is:

```text
Agent request budget
Tool-call budget
Output / semantic retry budget
HTTP attempt timeout
Provider transport retry budget
Concurrency budget
```

These are separate controls and should not be conflated.

---

## 11. Graceful degradation matters more than perfect completion

Web research contains expected partial failures: 403 pages, extraction failures, provider timeouts, irrelevant results, and individual model calls that exceed a budget.

A reliable pipeline should preserve the last validated state instead of throwing away several minutes of successful work because a later enrichment step failed.

P2.2 therefore distinguished:

```text
Fatal failure
→ no trustworthy basis remains for an answer

Enrichment failure
→ validated Evidence / Findings already exist
→ degrade and continue
```

Representative fallbacks became:

```text
selector fails
→ process all candidates in the batch

one extractor fails
→ skip that source; preserve the rest

Deep consolidation / sufficiency fails
→ fall back toward grounded capability

final synthesizer fails
→ deterministic rendering from validated state is still possible

no validated Evidence
→ GROUNDED / DEEP cannot claim a grounded answer
```

Pipeline reports now distinguish success, degraded completion, warnings, requested mode, final capability, and last validated checkpoint.

---

## 12. FAST / GROUNDED / DEEP is a capability ladder

The experiment eventually made one product lesson unavoidable: running the full Evidence / Finding / Sufficiency / gap loop for every ordinary question is too slow and too expensive.

The current research modes separate reliability levels:

```text
FAST
- small bounded tool budget
- automatic Source capture still happens
- direct model answer when possible
- source-only synthesis fallback if the research round ends before final text

GROUNDED
- one bounded research round
- Source selection
- isolated single-source Evidence extraction
- quote validation
- Evidence-only synthesis

DEEP
- GROUNDED foundation
- cross-source consolidation
- Findings / Conflicts
- sufficiency assessment
- optional gap-directed research
- final synthesis from validated research state
```

This is better understood as a **trust ladder** than merely “small / medium / large”:

```text
FAST
Tool results entered model context.

GROUNDED
Final factual statements must trace to validated Evidence.

DEEP
Grounding plus explicit cross-source conflict and research-completeness analysis.
```

The full DEEP pipeline is a reference capability, not the default path for a simple lookup.

---

## 13. Final synthesis must preserve provenance

Grounded final synthesis is restricted to validated Evidence rather than the raw research transcript.

A final statement chooses semantic content and Evidence IDs; the runtime validates that the referenced Evidence exists and renders citation markers mechanically.

The synthesizer also receives source metadata when it needs to name a source. This prevents a valid release-date Evidence item from being mislabeled as “SteamDB” when it actually came from the Steam store or another provider.

Research-state limitations are separated from factual Evidence statements. For example:

```text
“No direct SteamDB page could be read”
```

is a statement about the current run, not an external factual claim that should be artificially attached to an Evidence quote.

P2.2 also exposed a future Derived Fact problem: a synthesizer can perform apparently harmless transformations such as timezone or unit conversion that are not literally present in Evidence. For now, synthesis is instructed not to introduce such derived numeric/date facts unless they are explicitly supported. A future deterministic DerivedFact layer may handle this if the product needs it.

---

## What PydanticAI removed vs what Tsuzuri still owns

P2.1 already showed that PydanticAI removes large amounts of generic runtime work:

```text
JSON tool schemas
argument parsing
function dispatch
ToolCall/ToolReturn correlation
provider message formatting
structured outputs
RunContext injection
usage limits
framework retries / ModelRetry mechanics
```

P2.2 showed that the framework does **not** remove Tsuzuri's application semantics:

```text
what counts as a SourceCandidate
what should become Evidence
what a claim means
which sources are relevant
whether two claims are equivalent
what constitutes a conflict
whether the answer is sufficient
what degradation is acceptable
which research mode the product should use
```

A refined rule is:

> **Framework owns generic agent mechanics. Tsuzuri runtime owns invariants and product policy. The model owns semantic choices.**

The runtime should not become a second reasoning engine full of hard-coded research rules.

---

## The complexity lesson: logic boundaries are not call boundaries

The P2.2 laboratory implementation intentionally expanded until the active script approached several thousand lines.

That is useful evidence, not a target architecture.

The experiment separated many real semantic concerns:

```text
retrieval
source provenance
evidence extraction
quote validation
cross-source grouping
conflict detection
sufficiency
stopping
gap research
fallback
synthesis
metrics
```

But identifying separate **logical responsibilities** does not imply every responsibility must remain:

- a separate Agent instance;
- a separate model request;
- a separate public runtime layer;
- or a large imperative branch in `main.py`.

For example, production code may later remove the Selector for small candidate sets, combine some research-analysis outputs, or delegate workflow mechanics to a graph/workflow abstraction if that genuinely makes the control flow clearer.

The important result of P2.2 is knowing which semantic contracts must survive compression.

---

## What should be preserved after refactoring

The current implementation should be treated as a **laboratory reference**, not the shape that P2.3 should continue extending indefinitely.

The contracts worth preserving are:

1. retrieval attempts are captured mechanically;
2. Sources remain distinct from Evidence;
3. Evidence is source-specific and retains provenance;
4. support quotes are mechanically validated where possible;
5. single-source Evidence extraction is isolated from unrelated sources;
6. cross-source reasoning happens only after Evidence exists;
7. hard budgets and semantic sufficiency are different concepts;
8. enrichment failures preserve the last validated checkpoint;
9. final grounded synthesis sees validated research state, not the raw transcript;
10. FAST / GROUNDED / DEEP represent different product reliability/cost levels.

Everything else is eligible for simplification.

Before building P2.3 on top of this experiment, orchestration should be compressed into readable components such as:

```text
research/
  models.py
  state.py
  retrieval.py
  evidence.py
  analysis.py
  synthesis.py
  runtime.py
  modes.py
```

The top-level research flow should be readable in one screen. The goal is to keep the discovered contracts while removing laboratory ceremony.

---

## P2.2 answers

### Should `save_evidence` remain model-facing?

Not as the normal retrieval baseline. Automatic runtime capture prevents source loss; semantic extraction can happen afterward.

### Which fields should the runtime own?

Anything already mechanically known: IDs, tool identity, arguments, source URL/ID, observation linkage, source ownership, processing state, and citation rendering.

### What remains semantic?

Relevance, claim extraction, semantic entailment, equivalence/grouping, conflict meaning, sufficiency, research direction, and final wording.

### Should Observation, SourceCandidate, and Evidence remain distinct?

Yes. They answer different questions:

```text
Observation   → what happened during execution?
Source        → what retrievable material did that produce?
Evidence      → what source-specific fact is useful for this question?
```

### Did automatic Evidence improve reliability?

Yes, especially recall and provenance. It also adds model calls and latency, which is why GROUNDED / DEEP should be opt-in capability levels rather than universal ceremony.

### Did explicit stopping help?

Yes. Semantic sufficiency and bounded rounds are more useful than hoping one long Agent run will stop before hitting a hard tool limit.

### Should Tsuzuri adopt LangGraph now?

Not merely because the laboratory script became large. Much of the size is duplicated instrumentation and deliberately explicit experimentation. First compress the implementation while keeping the contracts above. Adopt a workflow/graph layer only if the resulting real application flow is still harder to understand than an explicit graph would be.

---

## P2.2 exit decision

**P2.2 is complete.**

Tsuzuri keeps PydanticAI and adopts runtime-guaranteed source capture plus semantic Evidence extraction as the preferred research direction.

The full experiment also establishes a FAST / GROUNDED / DEEP capability ladder, explicit runtime budgets, graceful degradation, and evidence-only synthesis.

Most importantly, it demonstrates a design limit: reliability mechanics can themselves become orchestration complexity. The next implementation should preserve the semantics while reducing the surface area.

P2.3 should therefore begin only after the P2.2 laboratory flow has been compressed into a readable baseline.

The Planning experiment should answer a new question rather than add more ceremony to the existing script:

> **When does an explicit plan improve a complex research task enough to justify another layer of state and control?**
