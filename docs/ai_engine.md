# AI engine (`src/ai_engine/`)

Local inference, embeddings and retrieval. Default engine is **oMLX**; Ollama is supported but off unless `PERSONAL_AI_OLLAMA_ENABLED=true`.
How retrieval quality is being improved: [rag_design.md](rag_design.md).

## Components

| File | Role |
|---|---|
| `engine.py` | Client factory: `build_chat_client()`, `build_embed_client()`, `client_kwargs_from_config()`. Always build clients here, never instantiate a client class directly. |
| `omlx_client.py` | oMLX client (OpenAI-compatible HTTP): chat, streaming, embeddings, tool calls. **Loopback hosts only** unless `PERSONAL_AI_ALLOW_REMOTE_OMLX=true`. |
| `ollama_client.py` | Ollama client (opt-in). |
| `embeddings.py` | Embedding wrapper: Qwen query instruction prefix, batched requests (`generate_embeddings`), `VaultEmbeddingCache` (vectors cached in the encrypted vault under internal `vault_embcache_*` keys; unchanged chunks are never re-embedded). |
| `chroma_store.py` | Line-packing / markdown-aware chunking (a small bill = 1 chunk), contextual header (`Provider \| Category \| Account \| Period \| Document`) embedded and stored with each chunk, deterministic chunk ids, index stamp (`embed_model`, `chunker_version`; `stale_reason()`), dimension check (fails closed), per-document replace, dense top-20 + BM25 fused with RRF, re-upload/superseded-version dedupe, IDF-coverage abstention. |
| `hybrid.py` | In-memory BM25, RRF and query-coverage (never written to disk). |
| `rag_engine.py` | Context assembly v2: one block per document, `[Source n \| provider \| category \| period \| id=ev_xxxxxxxx]`, rank-numbered, newest period first. |
| `grounding.py` | Deterministic answer check: every figure/date must occur in the *cited* block (`$1,234.50` = `1234.5`), citations must exist, figures must be cited. |
| `chat_engine.py` | Builds prompts (structured records → category-scoped RAG → plain RAG), budgets history/context (drops whole low-ranked blocks, never half a table), streaming, `rebuild_rag_index()`. Answers: nothing retrieved -> templated "cannot find" with **no model call**; otherwise temperature `PERSONAL_AI_RAG_QA_TEMPERATURE` (0.1), quote-and-cite prompt, grounding check with one bounded regenerate, then a visible "Could not verify" note (streaming: note only, no regenerate). Per-turn ids/scores/flags (no text) go to the encrypted activity log. |
| `query_analysis.py` | Detects category, provider and dates (one month or the span of several) in a question; `build_rag_filter` turns them into a Chroma `where` (`$and`; period overlap on `period_*_n`; documents with unknown period are never excluded). |

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `PERSONAL_AI_LLM_ENGINE`, `PERSONAL_AI_EMBED_ENGINE` | `omlx` | Engine choice |
| `PERSONAL_AI_OLLAMA_ENABLED` | `false` | Allow the Ollama path |
| `PERSONAL_AI_OMLX_HOST` | `http://127.0.0.1:8000` | oMLX server |
| `PERSONAL_AI_OMLX_CHAT_MODEL` | `Qwen3.8-27B-4bit` | Chat model |
| `PERSONAL_AI_OMLX_EMBED_MODEL` | `mlx-community/Qwen3-VL-Embedding-2B-8bit` | Embedding model (changing it requires a re-index) |
| `OMLX_API_KEY` | unset | Bearer token, read from the environment only, never logged |
| `PERSONAL_AI_OMLX_TOOLS` | `true` | Declare tool-calling support (oMLX exposes no capability flags) |
| `PERSONAL_AI_INFERENCE_CONCURRENCY` | `2` | App-wide cap on in-flight chat requests to the local server (all agents share it); see "Memory budget" |
| `PERSONAL_AI_OMLX_WORKER_MODEL` | unset (= chat model) | Optional smaller model for background roles: forecast specialists/ToT critics, quarantine extractor, verifier, planner |
| `PERSONAL_AI_CONTEXT_WINDOW_TOKENS` | `8192` | Prompt budget |
| `PERSONAL_AI_EMBED_BATCH_SIZE` | `32` | Texts per embeddings request |
| `PERSONAL_AI_RAG_HYBRID` | `true` | Dense + BM25 fusion |
| `PERSONAL_AI_RAG_MIN_COVERAGE` | `0.5` | Abstain when the best chunk covers less than this IDF-weighted share of the question's distinctive terms (calibrated with `python -m evals.rag.run --sweep`; never lower it to make an answer appear) |
| `PERSONAL_AI_RAG_QA_TEMPERATURE` | `0.1` | Sampling temperature for document Q&A |

All defaults live in `src/config.py`; add new settings there, not inline. Saved settings that name Ollama migrate to oMLX on read.

## Memory budget (single 128 GB Apple-silicon laptop)

All of this runs on one machine with unified memory, so the hard limit is RAM, not the number of agents.

**What consumes memory.** Every agent (coordinator workers, forecast specialists, quarantine extractor, verifier,
planner, chat) sends requests to **one** local oMLX server. More agents do not load more models. Memory grows with:

1. **Resident model weights.** A 27B model at 4 bits is roughly 15-17 GB. Each additional distinct model adds its
   own weights: for example ~5 GB for an 8B model at 4 bits, and ~2-3 GB for the embedding model.
2. **KV cache per in-flight request.** Per token this is 2 × layers × KV heads × head dim × bytes. For a typical
   27B-class model with grouped-query attention, that is on the order of 0.25 MB per token at 16-bit, or about
   2 GB for an 8k-token request. Four specialists in parallel on an ~5k-token evidence packet therefore add
   several GB.
3. **Server-side caching.** Prompt/KV caches that the server keeps between requests, plus MLX's buffer cache, can
   hold memory after a request finishes. A 60-70 GB reading for a 27B model is mostly (2) and (3), not weights.
   Check the server's own memory, cache and context-length settings in its documentation. The app cannot
   control them.

All figures above are estimates. Measure on the target laptop (below) before relying on them.

**What the app controls.**

- `PERSONAL_AI_INFERENCE_CONCURRENCY` (default 2) is a process-wide semaphore in `OmlxClient`. It covers both
  streamed and non-streamed chat, so at most N requests hold KV cache at once, regardless of how many agents
  or tickers are running.
  - Calls with a timeout (specialists) wait for a slot only up to their remaining deadline. If no slot frees,
    they fail visibly: the specialist is marked unavailable, the ToT node is tagged `critic unavailable`, and
    the finding is `uncertain`.
  - Interactive chat waits.
- `PERSONAL_AI_OMLX_WORKER_MODEL` moves background roles to a smaller model. This costs extra weights but makes
  each call several times faster, which matters because a cap of 2 runs the four specialists in two waves
  inside the 60 s specialist deadline.
- The Tree-of-Thought forecaster ([reasoning_design.md](reasoning_design.md)) makes no LLM calls when its first,
  rule-based round is decisive. Otherwise it makes one shared critic round of at most 4 calls per ticker.

**Suggested profiles.** These are starting points, not measurements.

| Profile | Chat model | Worker model | Concurrency | Resident weights (est.) | Trade-off |
|---|---|---|---|---|---|
| Lean (default) | 27B 4-bit | unset | 2 | ~17 GB + embeddings | One model; specialists may time out on a 27B model |
| **Recommended** | 27B 4-bit | ~8B 4-bit, same family | 2 | ~22 GB + embeddings | Fast critics; chat quality unchanged |
| Small | ~14B 4-bit | unset or ~8B | 3 | ~9-14 GB + embeddings | Most headroom; weaker chat answers |

Avoid two large models resident at the same time, and avoid raising concurrency to buy speed. Use the worker model
for speed instead.

**Measure before trusting a profile.** On the personal laptop:

1. Open Activity Monitor and watch the Memory tab: the oMLX server process and the overall memory pressure.
2. Run a document chat, a Coordinator question naming three tickers, and a Forecast-page specialist run.
3. Record the peak memory for each run.

A profile is acceptable when memory pressure stays out of red and swap does not grow. Record the result in
[platform_plan.md](platform_plan.md) backlog B19.

## Using it

```python
from src.ai_engine.chat_engine import ChatEngine
from getpass import getpass
from src.security.auth import AuthManager

# Use an already registered vault; never put a password in source code.
encryption_key = AuthManager().authenticate(getpass("Vault password: "))
chat_engine = ChatEngine(encryption_key=encryption_key)

# Add data to encrypted vault
chat_engine.add_data_to_vault("my_note", {
    "title": "Important Note",
    "content": "This is my sensitive data"
})

# Initialize RAG with vault data
chat_engine.initialize_rag()

# Query the vault
result = chat_engine.query_vault("What is in my vault?")
print(result["response"])
```

Main `ChatEngine` methods: `initialize_rag()`, `rebuild_rag_index()` (drop and re-embed everything), `query_vault(query, use_rag=True, history=None)`,
`query_vault_stream(...)`, `get_structured_records(category, provider, start_date, end_date)`, `estimate_usage(history)` (context meter).

**Index lifetime:** the Chroma client is in-memory (the `.chroma` directory stays empty) and the index is rebuilt from the vault each session; the vault-backed embedding cache makes that rebuild free when nothing changed. Chunk format changes bump `CHUNKER_VERSION` (cache key salt + index stamp).

**Re-indexing:** after changing the embedding model, use *Chat → Advanced → Rebuild RAG Index*. It clears the collection and re-embeds every vault
document; if embedding fails midway you get an explicit error and must run it again (the index is empty until then).

## Tool calling over oMLX

The agent runtime speaks Ollama-shaped messages. `OmlxClient` converts to/from the OpenAI shape (ids minted and matched to tool results,
JSON-string arguments parsed to dicts). If your model has no tool support set `PERSONAL_AI_OMLX_TOOLS=false`; agents then run without tools.
Not yet verified against a live server: streaming tool-call deltas and the `response_format` JSON-schema shape (backlog B1 in [platform_plan.md](platform_plan.md)).

`make demo` is a live auth/vault/model smoke flow, not an offline test.
Run `make check-context` after chat-path/model changes; an unavailable
configured model/service is an explicit integration-check blocker.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "oMLX host must be loopback" | Host in settings is not 127.0.0.1/localhost; fix it, or opt in with `PERSONAL_AI_ALLOW_REMOTE_OMLX=true` |
| "Ollama is disabled" | A saved/explicit engine is `ollama`; use oMLX or enable Ollama |
| Connection refused / URL error | oMLX is not running or the model is not loaded |
| Answers ignore new documents | Re-upload finished? Run *Rebuild RAG Index* |
| Odd retrieval after switching embedding model | You switched models without re-indexing |
| `make check-context` warns | Context-window setting larger than the model supports |

## Tests

`make test` (ChatEngine with fake clients, ChromaStore offline, oMLX/Ollama client doubles; `tests/test_rag_*.py` cover embeddings/cache, retrieval, context, grounding and run the `make eval-rag` gates). `make eval-rag` -> `evals/results/rag_latest.md` (synthetic bills, hashed bag-of-words stand-in embedder: read the deltas, not the absolutes; it has not been run against the real oMLX embedding model). `make check-context` validates context-window assumptions.
