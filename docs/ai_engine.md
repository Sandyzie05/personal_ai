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
| `PERSONAL_AI_CONTEXT_WINDOW_TOKENS` | `8192` | Prompt budget |
| `PERSONAL_AI_EMBED_BATCH_SIZE` | `32` | Texts per embeddings request |
| `PERSONAL_AI_RAG_HYBRID` | `true` | Dense + BM25 fusion |
| `PERSONAL_AI_RAG_MIN_COVERAGE` | `0.5` | Abstain when the best chunk covers less than this IDF-weighted share of the question's distinctive terms (calibrated with `python -m evals.rag.run --sweep`; never lower it to make an answer appear) |
| `PERSONAL_AI_RAG_QA_TEMPERATURE` | `0.1` | Sampling temperature for document Q&A |

All defaults live in `src/config.py`; add new settings there, not inline. Saved settings that name Ollama migrate to oMLX on read.

## Using it

```python
from src.ai_engine.chat_engine import ChatEngine
from src.security.encryption import AEADEncryption

chat = ChatEngine(encryption_key=AEADEncryption().key)   # real use goes through the login gate, see the UI
chat.initialize_rag()                                    # index vault documents
result = chat.query_vault("What was my gas bill in September?")
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
