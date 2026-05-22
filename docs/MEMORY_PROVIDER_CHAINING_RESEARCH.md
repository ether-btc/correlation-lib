# Memory Provider Chaining Research: Integrating correlation-lib with Mnemosyne

**Date:** 2025-05-22
**Status:** Research Complete — Architectural Recommendation Ready

---

## Executive Summary

After analyzing the Hermes Agent memory provider architecture, production agent
frameworks (LangChain, Mem0, AutoGPT), and the existing Mnemosyne/correlation-lib
code, the clear recommendation is:

**Use the Composition/Delegation Pattern: ONE provider registered with
MemoryManager that internally wraps Mnemosyne + correlation-lib.**

This is the only pattern that works within Hermes' hard one-provider constraint
(`MemoryManager.add_provider` rejects a second external provider), avoids the
double-injection failure modes Mnemosyne already suffered through (C13 fix),
and keeps both systems decoupled.

---

## 1. How Production Agent Frameworks Handle Memory Enrichment Layers

### LangChain / LangGraph (2026)

LangChain's modern approach uses `BaseStore` as a single abstraction with
composable backends. Enrichment is NOT done by stacking providers — it's done by:

- **Memory-as-a-tool**: The agent decides when to read/write memory via tool calls
- **Background extraction nodes**: Separate graph nodes extract facts from
  conversation turns and write to the store
- **Context injection**: A single `MessagesPlaceholder` in the prompt template
  receives merged context from ONE store

Key insight: LangGraph does NOT support multiple memory providers. The `BaseStore`
is singular. Enrichment happens at the extraction/storage layer, not at the
provider layer. They use composition inside the store, not multiple stores.

### Mem0

Mem0 is a monolithic memory layer with built-in enrichment:
- Vector-first storage with optional graph enhancement (Mem0g)
- Enrichment is internal: fact extraction, deduplication, relevance scoring
- External integrations (Haystack, LangChain) wrap Mem0 as a single store
- No stacking of multiple Mem0 instances

Key insight: Mem0 treats enrichment as an internal concern, not a provider-layer
composition. This is the same pattern we should use.

### AutoGPT (Platform)

AutoGPT's memory architecture uses a single `MemoryProvider` interface with:
- One active backend at a time (SQLite, Pinecone, Redis, etc.)
- Enrichment via preprocessing hooks (fact extraction before storage)
- Context injection via a single prompt assembly step

Key insight: All three frameworks converge on ONE provider with internal
composition for enrichment.

### Amazon Bedrock AgentCore Memory (2026)

AWS's approach mirrors this exactly:
- Single managed memory service
- Internal pipeline: short-term → extraction → long-term → consolidation
- Enrichment (structured construction, knowledge distillation) happens INSIDE
  the pipeline, not by stacking services

---

## 2. Decorator vs Composition vs Middleware Patterns

### Decorator Pattern (Wrapper/Proxy)

```python
class EnrichingProvider(MemoryProvider):
    def __init__(self, base: MemoryProvider):
        self._base = base  # Mnemosyne
        self._enricher = CorrelationEngine()

    def prefetch(self, query, **kw):
        base_result = self._base.prefetch(query, **kw)
        enriched = self._enricher.enrich(query, base_result)
        return enriched
```

**Pros:**
- Transparent wrapping — base provider unchanged
- Every MemoryProvider method can be intercepted
- Works with Hermes' one-provider constraint

**Cons:**
- Must forward ALL ABC methods (initialize, shutdown, get_tool_schemas,
  handle_tool_call, on_turn_start, on_session_end, on_session_switch,
  on_pre_compress, on_memory_write, on_delegation, etc.)
- Fragile — if MemoryProvider ABC adds methods, decorator breaks silently
- Tool routing ambiguity: enrichment layer's tools + base provider's tools
  both need dispatch

**Verdict:** Viable but maintenance-heavy due to ABC forwarding burden.

### Composition Pattern (Internal Delegation)

```python
class CorrelatingMnemosyneProvider(MemoryProvider):
    def __init__(self):
        self._mnemosyne = MnemosyneMemoryProvider()
        self._correlation = CorrelationEngine()

    def initialize(self, session_id, **kwargs):
        self._mnemosyne.initialize(session_id, **kwargs)
        # Wire correlation to Mnemosyne's beam
        self._correlation.initialize(...)

    def prefetch(self, query, **kw):
        base = self._mnemosyne.prefetch(query, **kw)
        enriched = self._correlation.enrich(query, base_context=base)
        return self._merge(base, enriched)

    def get_tool_schemas(self):
        return self._mnemosyne.get_tool_schemas()  # Pass-through

    def handle_tool_call(self, tool_name, args, **kw):
        return self._mnemosyne.handle_tool_call(tool_name, args, **kw)
```

**Pros:**
- Explicit delegation — caller controls what goes where
- Tool schemas come from Mnemosyne only (correlation is context-only)
- Enrichment runs AFTER base prefetch, avoiding double-injection
- Can pass Mnemosyne's beam directly to correlation backends
- Easy to disable correlation (skip enrichment, delegate to Mnemosyne)
- Testable — both components can be tested independently

**Cons:**
- Still needs to forward ABC methods (but fewer, since correlation has no tools)
- Requires importing MnemosyneMemoryProvider directly

**Verdict:** BEST fit for this use case. Explicit, debuggable, avoids
double-injection by construction.

### Middleware Pattern (Interceptor Chain)

```python
class MemoryMiddleware:
    def before_prefetch(self, query): ...
    def after_prefetch(self, query, result): ...
    def before_tool_call(self, name, args): ...

class MemoryManager:
    def prefetch_all(self, query, **kw):
        for mw in self._middleware:
            query = mw.before_prefetch(query)
        result = self._provider.prefetch(query, **kw)
        for mw in reversed(self._middleware):
            result = mw.after_prefetch(query, result)
        return result
```

**Pros:**
- Most flexible — any number of enrichment layers
- Clean separation — middleware is invisible to provider
- Reusable across different providers

**Cons:**
- Requires changes to MemoryManager (upstream code changes)
- Ordering dependencies between middleware can be subtle
- MemoryManager doesn't currently support middleware
- Overkill for a two-layer composition

**Verdict:** Best for framework-level extensibility, but too invasive for
our use case. Would require changes to hermes-agent core.

---

## 3. Mnemosyne's MemoryProvider ABC: Chaining Support Analysis

### Architecture Review

The `MemoryProvider` ABC at `/home/hermes-pi/.hermes/hermes-agent/agent/memory_provider.py`
defines:

**Required (abstract):**
- `name` (property)
- `is_available()`
- `initialize(session_id, **kwargs)`
- `get_tool_schemas()`

**Optional (default implementations):**
- `system_prompt_block()` → ""
- `prefetch(query)` → ""
- `queue_prefetch(query)` → noop
- `sync_turn(user, asst)` → noop
- `handle_tool_call(tool_name, args)` → NotImplementedError
- `shutdown()` → noop
- `on_turn_start(turn, message)` → noop
- `on_session_end(messages)` → noop
- `on_session_switch(new_session_id)` → noop
- `on_pre_compress(messages)` → ""
- `on_memory_write(action, target, content, metadata)` → noop
- `on_delegation(task, result)` → noop
- `get_config_schema()` → []
- `save_config(values, hermes_home)` → noop

### Supports Chaining?

**No explicit support.** The ABC is designed for a single provider, not a chain.
There is no `_next` pointer, no middleware hooks, no enrichment protocol.

**However:** The ABC's use of default implementations means a composition
provider only needs to override methods where enrichment is needed. Methods
like `get_tool_schemas()` and `handle_tool_call()` can be delegated directly
to the wrapped Mnemosyne instance.

### MemoryManager's Role

The `MemoryManager` at `/home/hermes-pi/.hermes/hermes-agent/agent/memory_manager.py`:

- Always includes the builtin provider (`name == "builtin"`)
- Allows exactly ONE external provider (enforced at line 267-279)
- Merges `prefetch_all()` from all registered providers with `"\n\n".join()`
- Merges `build_system_prompt()` from all providers with `"\n\n".join()`
- Routes tool calls by tool name → provider mapping

**Critical finding:** The one-external-provider limit is the binding constraint.
It is enforced in `MemoryManager.add_provider()` and in `agent_init.py` lines
982-1043 which calls `_load_mem(provider_name)` exactly once.

### What This Means

To use both Mnemosyne and correlation-lib, we have exactly ONE registration
slot. The registered provider MUST internally handle both concerns. This
leaves two options:

1. **Composition provider** (recommended): A thin wrapper that holds both
   MnemosyneMemoryProvider and CorrelationEngine, delegating appropriately
2. **Modified Mnemosyne provider**: Add correlation hooks directly into
   MnemosyneMemoryProvider (fragile coupling, not recommended)

---

## 4. Known Failure Modes: Dual Memory Provider Context Injection

### Failure Mode 1: Double-Injection (The C13 Bug)

This exact bug was found and fixed in Mnemosyne (lines 37-68 of `__init__.py`).

**What happened:** When Mnemosyne was registered BOTH as a MemoryProvider AND
as a legacy hermes_plugin, two independent pre-LLM-call hooks fired:
1. `MnemosyneMemoryProvider.prefetch()` → "## Mnemosyne Context"
2. `hermes_plugin._on_pre_llm_call()` → "MNEMOSYNE CONTEXT / MNEMOSYNE RECALL"

**Impact:** Token cost doubled every turn. Agent confused by duplicated facts
with slightly different formatting. No crash — silent degradation.

**Fix:** Module-level `_provider_active` flag with refcount semantics. When
the MemoryProvider is active, the plugin path defers.

**Lesson for correlation-lib:** Any pattern where two code paths both call
`beam.recall()` and inject context will cause this. The composition pattern
prevents it by construction — only ONE prefetch() path runs.

### Failure Mode 2: Context Pollution

When enrichment adds low-quality or irrelevant context:
- Agent follows enriched suggestions instead of user intent
- Enriched context competes with recalled memories for attention
- Higher-importance enriched content can drown out actually relevant memories

**Mitigations:**
- Correlation-lib already uses confidence thresholds (>=0.9 for prefetch)
- Mnemosyne already filters low-relevance prefetch results (MIN_SCORE_THRESHOLD=0.15)
- The composition provider should enforce a total token budget for enrichment
- Enrichment should be APPENDED to (not replace) base context

### Failure Mode 3: Token Budget Exhaustion

With 200K token context windows, this seems unlikely but:
- Mnemosyne prefetch: up to 8 memories × ~200 chars ≈ 2K tokens
- Correlation enrichment: potentially unbounded if rules fire broadly
- System prompt blocks: each provider adds ~100-500 tokens
- Combined: could push a near-full context window over the limit

**Mitigations:**
- Hard token limit on enrichment output (e.g., 500 tokens max)
- Enrichment should be capped by count of injected items
- The composition provider should truncate enrichment before base context

### Failure Mode 4: Write Path Conflicts

If both providers call `beam.remember()` on the same turn:
- Duplicate memories stored (one from sync_turn, one from on_turn_start)
- Stale enrichment-written memories persisting after rule invalidation
- Different importance scoring for the same fact

**Mitigations:**
- Only Mnemosyne should own the write path (sync_turn, handle_tool_call)
- Correlation-lib should be READ-ONLY — never write to beam
- The composition provider enforces this separation

### Failure Mode 5: Lifecycle Race Conditions

With two providers handling lifecycle events independently:
- `initialize()` order dependency (correlation needs beam from Mnemosyne)
- `shutdown()` race (correlation's daemon thread vs Mnemosyne's sleep thread)
- `on_session_switch()` needs to propagate to both in correct order

**Mitigations:**
- Composition provider controls initialization order explicitly
- Mnemosyne init FIRST, then wire correlation to the beam
- Shutdown in reverse order (correlation first, then Mnemosyne)

---

## 5. Memory Provider Stacking: One Delegating Provider vs Coordinator

### Option A: One Provider with Internal Delegation (RECOMMENDED)

```
MemoryManager
    └── CorrelatingMnemosyneProvider (registered as "mnemosyne")
            ├── MnemosyneMemoryProvider (internal, not registered)
            └── CorrelationEngine (internal)
```

**Why this wins:**
1. **No changes to hermes-agent core** — works with existing plugin system
2. **No changes to MemoryManager** — one provider, one registration
3. **No double-injection** — single prefetch() path merges both outputs
4. **Tool schemas clean** — only Mnemosyne's tools exposed (correlation is context-only)
5. **Lifecycle controlled** — initialization order guaranteed
6. **Deployed as a drop-in replacement** — just change which `__init__.py` loads

**Implementation approach:**
- CorrelatingMnemosyneProvider lives in the correlation-lib hermes adapter
- It imports MnemosyneMemoryProvider and wraps it
- Registered as provider "mnemosyne" in config (or new name "enriched-mnemosyne")
- Falls back to raw Mnemosyne if correlation engine fails to initialize

### Option B: Multiple Providers with Coordinator (REJECTED)

```
MemoryManager
    ├── MnemosyneMemoryProvider (external provider)
    └── CorrelationMemoryProvider (external provider) ← REJECTED
```

**Why this fails:**
1. `MemoryManager.add_provider()` rejects second external provider (line 267-279)
2. Would require modifying MemoryManager to support multiple external providers
3. Double-injection in prefetch_all() — both providers' prefetch() would fire
4. Tool name conflicts — both might claim the same tool names
5. system_prompt_block() merges blindly with "\n\n".join() — no coordination
6. Changes to core hermes-agent code — high risk, hard to upstream

### Option C: Context Engine Plugin (VIABLE ALTERNATIVE)

Hermes has a `plugins/context_engine/` directory for context enrichment plugins
that are separate from memory providers. This would allow:

```
MemoryManager
    └── MnemosyneMemoryProvider (memory provider, unchanged)

ContextEngine (separate plugin system)
    └── CorrelationContextEngine (new context engine plugin)
```

**Pros:**
- Completely separate from memory provider system
- No changes to either Mnemosyne or correlation-lib
- Correlation runs as an independent enrichment layer

**Cons:**
- Requires understanding the context engine plugin API
- May not have access to the same lifecycle hooks (on_turn_start, etc.)
- Less control over injection ordering
- May not exist or be mature enough (needs investigation)

---

## Recommended Implementation Plan

### Phase 1: Composition Provider (Minimal Viable)

Create `CorrelatingMnemosyneProvider` that:
1. Instantiates `MnemosyneMemoryProvider` internally
2. Delegates ALL tool calls and schemas to Mnemosyne
3. Adds correlation enrichment in `prefetch()` (after Mnemosyne's prefetch)
4. Adds correlation trigger in `on_turn_start()` (new task detection)
5. Passes all other lifecycle hooks through to Mnemosyne
6. Fails gracefully — if correlation init fails, Mnemosyne still works

### Key Design Decisions

1. **Enrichment is APPENDED, never replaces base context**
2. **Token budget: max 500 tokens for enrichment per turn**
3. **Correlation is READ-ONLY** — never writes to Mnemosyne
4. **Mnemosyne init happens FIRST** — correlation depends on beam
5. **Graceful degradation** — correlation failures don't break memory

### Files to Create/Modify

1. `/home/hermes-pi/correlation-lib/correlation_lib_adapters/hermes/composition_provider.py`
   — New composition provider
2. `/home/hermes-pi/.hermes/plugins/mnemosyne/__init__.py`
   — Optionally add a hook for enrichment plugins (if desired)
3. Config: `memory.provider: enriched-mnemosyne` or override mnemosyne discovery

---

## Appendix: Code References

### The One-Provider Constraint (agent_init.py:982-1043)

```python
# Memory provider plugin (external — one at a time, alongside built-in)
agent._memory_manager = None
if not skip_memory:
    _mem_provider_name = mem_config.get("provider", "")
    if _mem_provider_name and _mem_provider_name.strip():
        agent._memory_manager = _MemoryManager()
        _mp = _load_mem(_mem_provider_name)  # Loads ONE provider
        if _mp and _mp.is_available():
            agent._memory_manager.add_provider(_mp)  # Only ONE external
```

### The Rejection (memory_manager.py:258-279)

```python
def add_provider(self, provider):
    is_builtin = provider.name == "builtin"
    if not is_builtin:
        if self._has_external:
            logger.warning(
                "Rejected memory provider '%s' — external provider '%s' is "
                "already registered. Only one external memory provider is "
                "allowed at a time.",
                provider.name, existing,
            )
            return
        self._has_external = True
```

### The Merge (memory_manager.py:339-356)

```python
def prefetch_all(self, query, *, session_id=""):
    parts = []
    for provider in self._providers:
        result = provider.prefetch(query, session_id=session_id)
        if result and result.strip():
            parts.append(result)
    return "\n\n".join(parts)  # Blind merge — no dedup, no budget
```
