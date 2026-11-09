# RAG quality, freshness and bill ingestion: diagnosis, design, executable plan

**Audience: a coding agent (or human) with no prior context.** Read `docs/platform_plan.md` §0-§2 first (rules, invariants).
Manual test plan for this area: [user_testing_guide.md](user_testing_guide.md) Part A. Index: [README.md](README.md).
Status: **R0-R6 implemented and measured offline (2026-10-25)**; R7-R11 (ingestion) see §8 status. Sections 1-2 describe the *pre-R0* code (kept as the diagnosis); §8 has what changed. Results: `evals/results/rag_latest.md`. **Not yet verified against the live oMLX embedding model** (offline eval uses a hashed bag-of-words stand-in).

**Architecture summary:** today's document path is Naive RAG; the target is Advanced RAG (pre-retrieval filters, hybrid retrieval, rerank, grounding check) inside an already-Modular system (§8.1).

## 1. Current implementation (answers to the four questions)

| Question | Answer (with evidence) |
|---|---|
| **Encoder** | `mlx-community/Qwen3-VL-Embedding-2B-8bit` served by oMLX (`config.py::PERSONAL_AI_OMLX_EMBED_MODEL`, context 32 768 tokens). Embeddings are requested **one chunk per HTTP call** (`omlx_client.py::generate_embedding`, input `[text]`; `chroma_store.add_documents` loops). **No query instruction prefix**: `embeddings.py` only adds prefixes for `nomic-embed*`, so Qwen queries and documents are embedded identically (Qwen3 embedding models are trained with an `Instruct: ...\nQuery: ...` query format, so retrieval is weaker than it should be). |
| **Chunking** | `chroma_store.add_documents` calls `_chunk_text(doc, chunk_chars)` with `chunk_chars = min(1000 tokens * 4, HARD_CHUNK_CHAR_CAP=2000) = 2000` chars. `_chunk_text` splits on `\n` and **emits every line as its own chunk** when the line fits; it only packs/overlaps words inside a single over-long line. Measured on a 12-line synthetic bill: **10 chunks** (`'# Rocky Mountain Power'`, `'Account: 12345'`, `'\| Item \| Amount \|'`, `'\|---\|---\|'`, ...). The better, markdown-aware `chunk_text()` / `_chunk_markdown()` (table-aware, overlapping) exist in the same file but **are never called** (dead code). Chunk metadata is only `storage_key`, `category`, `provider`, `chunk` (no title, period, page, doc date, content hash). |
| **Similarity** | Chroma collection `personal_ai_vault_v4`, `hnsw:space = cosine`; distance converted to similarity (`1 - d`); filter `min_relevance = 0.20` (`config.DEFAULT_MIN_RELATIVE_SCORE`); `top_k = 5`; at most 3 chunks per document (`DEFAULT_MAX_PER_DOCUMENT`). Dense retrieval only: no keyword/BM25, no reranker, no MMR. |
| **Context assembly** | Order of paths in `chat_engine._build_prompt`: (1) if the query names a category (+ provider, relative date) and structured extracted records exist, answer from **structured records** (deterministic numbers); (2) else category-scoped vector search via Chroma `where`; (3) else unscoped. Retrieved chunks are joined as `[Source i: chunk #i · provider]\n<chunk>` in **score order** (not document/page order), then `Context from your data:\n...\nAnswer based on the context above...` (`rag_engine.get_context_for_query`, `chat_engine._finalize_prompt`). Document text is `_data_to_text`: `text_content` plus every metadata/extraction field flattened as `key: value` joined by spaces. Generation temperature default 0.3. |

## 2. Why answers are inaccurate and the model hallucinates (root causes, ranked)

1. **Line-level chunking (P0).** A chunk like `Total due: $69.12` or a bare table row has no provider, period or account. Dense retrieval ranks
   such fragments by surface similarity; the model then sees disconnected lines, mixes numbers across bills, or fills gaps. The
   architecture note in the brief applies exactly: chunking is an architectural decision that sets hallucination risk. Tiny bills
   (<1.5k tokens) should usually be **one chunk**, not ten.
2. **No contextual header on chunks (P0).** Even correct chunks lack "who/what/when". Provider/period must be inside the embedded text, not only in metadata.
3. **Wrong/unspecific query embedding (P1).** No Qwen instruction prefix; queries and documents live in slightly different spaces.
4. **Dense-only retrieval on numeric/identifier-heavy documents (P1).** Amounts, account suffixes, dates and provider names are lexical; hybrid (dense + BM25) is the standard fix.
5. **Uncalibrated thresholds (P1).** `min_relevance=0.20` is very permissive for this model (everything passes), so weak chunks reach the prompt; `k=5` over-fetch plus no rerank.
6. **Score-ordered, unlabeled context (P2).** Sources lack period/page; no ordering by document; duplicates of the same bill from re-uploads are not collapsed.
7. **No grounding verification (P1).** Nothing checks that numbers/dates in the answer occur in the context; no citation requirement; no abstention-quality measurement.
8. **Staleness (P1).** Documents arrive manually; there is no manifest, no "what is missing/overdue", no incremental indexing (every session `initialize_rag` re-embeds all docs, now one HTTP call per chunk), no embedding/chunker version stamp.
9. **No retrieval eval (P0 to enable all tuning).** Without a golden Q/A set, chunk size/threshold/k changes are guesses.

## 3. Target design

### 3.1 Parsing and storage (keep what works)
- Keep `pymupdf4llm` (markdown with tables, reading order). Add per-page markers (`<!-- page N -->`) so chunks can cite pages. Optional OCR fallback for image-only PDFs (opt-in, local).
- Vault remains the source of truth (encrypted). Index is derived and rebuildable. **Content hash** (sha256 of file bytes) per document in a vault **ingestion manifest** (§4).

### 3.2 Chunking (structure-aware, parent-child, header-prefixed)
1. **Small documents (<= ~1 500 tokens, most bills): one chunk = the whole document** (plus header). Whole-document context removes cross-line fragmentation.
2. **Larger documents:** split by markdown structure: heading sections first, tables kept whole (split only at row boundaries, repeating the table header row), then paragraphs; target **300-500 tokens** per chunk (1 200-2 000 chars), **10-15% overlap** only when a section must be split mid-text. Never one-line chunks (min 150 chars: merge forward).
3. **Contextual header** prepended to every chunk *before embedding and stored with the chunk*:
   `Provider: Rocky Mountain Power | Category: electricity | Period: 2026-09-01..2026-09-30 | Document: <title> | Page 2/3`.
   Fields come from structured extraction (`data_extraction`) and upload metadata; missing fields are omitted, never guessed.
4. **Parent link:** each chunk stores `doc_id` (storage key), `section_id`, `page`, `chunk_index`, `parent_text_hash`; retrieval can expand to the whole parent section/document when a hit is a fragment.
5. **Chunk-size is an experiment, not a constant:** `chunker_version` and parameters are stamped in the collection metadata; `make eval-rag` compares configs (whole-doc vs 400 vs 800 tokens) on the golden set before the default changes.

### 3.3 Embedding
- Batch requests (list `input`, 16-64 per call) instead of one call per chunk; cache embeddings by `sha256(header+chunk+model_id+chunker_version)` so unchanged chunks are never re-embedded.
- Queries use the model's instruction format: `Instruct: Given a question about the user's own bills and statements, retrieve the passages that answer it\nQuery: <question>`; documents are embedded without instruction. Verify vector dimension and normalisation at startup (fail closed on mismatch).
- Collection metadata records `embed_model`, `dim`, `chunker_version`; a mismatch triggers "index is stale: rebuild" (never silent mixing; see `ChromaStore.reset`).

### 3.4 Retrieval pipeline
1. **Query analysis** (exists: `query_analysis.py`) → category, provider, relative date range → **metadata filters** including `period_start/period_end` (add to chunk metadata) so "last month's gas bill" filters by period instead of hoping similarity finds it.
2. **Hybrid candidates:** dense top-20 + BM25/keyword top-20 (SQLite FTS5 over chunk text kept **inside the encrypted vault**, or in-memory rebuilt at unlock; see security S-R3), fused with reciprocal rank fusion.
3. **Rerank** to top 4-6: local reranker model via oMLX if one is available (e.g. a Qwen3 reranker); otherwise MMR for diversity plus a lexical-overlap tiebreak. Reranker is optional and measured; skip if it does not improve the eval.
4. **Calibrated abstention:** thresholds set from the eval set (not 0.20); if no chunk passes, return the not-found path *without calling the model for an answer*.
5. **Dedupe:** collapse identical-hash documents (re-downloads) and prefer the newest superseding document for the same provider+period.

### 3.5 Context assembly (what the model sees)
- Group chunks by document, order by `(period desc, page asc)`, one labeled block per document:
  `[Source 2 | Rocky Mountain Power | electricity | 2026-09 | p.1-2 | id=ev_ab12]` + text. Stable **evidence ids** (`ev_<hash8>`) are the citation handles.
- Hard token budget (existing `_total_budget_chars`), reserving history/headroom; drop lowest-ranked blocks, never truncate mid-table.
- Structured path stays first for arithmetic: totals/deltas come from deterministic code over extracted fields, and the model only phrases them.

### 3.6 Answer-time hallucination controls
1. Prompt: answer only from context; **quote the figure and cite `[ev_id]`**; if two sources disagree, show both and ask; if absent say "not found in your documents" (templated). Temperature 0.1 for QA.
2. **Numeric/date grounding check** (deterministic, post-generation): every number, date and currency amount in the answer must appear in the cited context (normalised: `$1,234.50` = `1234.5`); otherwise append a visible "could not verify: ..." note or regenerate once with the offending claims flagged. This is the single most effective guard against fabricated figures.
3. **Citation check:** answer sentences carrying figures must cite an evidence id that exists in the context.
4. Log per-turn retrieval diagnostics (query, filters, ids, scores, abstained?, grounding result) to the **encrypted** activity log (ids and scores only, no text).

### 3.7 Evaluation (build first; everything else is tuned against it)
- Synthetic bill corpus generator (PDF/markdown, 3 providers x 12 months, with planted traps: duplicate re-uploads, two accounts, credits, similar amounts, corrected bill) + ~80 golden questions (lookup, comparison, aggregation, absent, conflicting).
- Metrics: recall@k, MRR, context precision, **faithfulness = numeric-grounding pass rate**, abstention accuracy (absent questions refused), latency, tokens. Hard gates in `make eval-rag` (targets: recall@5 >= 0.95, faithfulness >= 0.98, abstention >= 0.95; set after the baseline run, never lowered to pass).
- Baseline run on the *current* pipeline is recorded first (expected: low context precision from line chunks) so improvements are measurable.

## 4. Keeping the index up to date (ingestion pipeline)

The laptop is not always on, so freshness is **pull-on-demand with a visible ledger**, not a daemon.

```
[Sync now] / app open ─► expected-bills ledger ─► fetchers ─► validate ─► store (encrypted) ─► extract ─► chunk ─► embed (batch, cached) ─► verify ─► report
                           (what is overdue)   Gmail | Portal | manual     magic bytes,    vault        existing   header+      new/changed   self-retrieval   new/failed/
                                                                          size, hash                    pipeline   structure    chunks only   smoke test       needs-you
```

1. **Ingestion manifest** (vault key `ingest_manifest`, internal): per document `{sha256, source(gmail|portal|manual), source_ref, provider, category, period_start, period_end, fetched_at, status(new|indexed|failed|superseded), chunker_version, embed_model}`. Duplicate hash ⇒ skip (idempotent); same provider+period, new hash ⇒ mark old `superseded` (kept, not indexed) and surface "corrected bill".
2. **Expected-bills ledger** (vault key `ingest_expectations`): per provider cadence (monthly/quarterly) → "electricity: last bill Aug, September overdue 12 days". Drives the Sync prompt and the Dashboard badge; no network needed.
3. **Incremental indexing:** index only `status=new` docs; delete a document's chunks by `doc_id` before re-embedding; a full rebuild is only for `embed_model`/`chunker_version` change. Batch embeddings; embedding cache.
4. **Verify step:** for every newly indexed doc run a self-retrieval smoke test ("`<provider> <period> total`" must return that doc in top-3 and its extracted total must equal a number in the retrieved chunk); failures are reported, doc stays `failed` (never silently half-indexed).
5. **Report:** "3 new (electricity Sep, gas Sep, phone Sep), 1 duplicate skipped, 1 needs you (login challenge)". Proposals only; nothing changes memory without approval (S4).
6. **Sources, in order of preference:**
   - **Gmail attachments (preferred for everything that emails a bill).** Ask each provider to enable "paperless/bill ready" emails to Gmail; then one path covers utilities and other platforms. Read-only: Gmail API scope `gmail.readonly` (or an MCP server exposing `get_attachment`; **verify attachment support first, card R7**), search by sender allowlist + `has:attachment filename:pdf`, download attachments only, never body text into prompts. Attachments are untrusted files (see §6).
   - **Portal download agent** for electricity, gas, phone portals that do not email PDFs (§5).
   - **Manual upload** (exists) always available; same pipeline.

## 5. Can a local AI model open a browser, log in and fetch bills? (feasibility and plan)

**Yes, technically, but it should not be the LLM's job to log in.** Three designs, in order of recommendation:

| Option | How | Verdict |
|---|---|---|
| **A. Deterministic Playwright recipe per provider (recommended)** | A small script (selectors recorded once with `playwright codegen`) logs in with credentials injected by code, navigates to the bills page, downloads the newest N PDFs. The local LLM is **not in the loop** for login/navigation. | Fast, reliable, auditable, safest. Breaks when the site changes → alert user, fall back to B for repair. |
| **B. LLM-assisted repair/recording** | When a recipe step fails, a local vision-language model (Qwen3-VL via oMLX) looks at a screenshot/DOM *of an allowlisted page* and proposes new selectors; a human approves the patched recipe once; it is replayed deterministically thereafter. | Good compromise; LLM output is data (a selector), never executed code. |
| **C. Fully autonomous LLM "computer-use" browsing** | A local model decides every click from screenshots each run. | Not recommended: slow on a laptop, brittle, and a prompt-injection surface (a page can talk to the agent). Only for experiments, never with credentials. |

**Hard constraints for any browser agent (security):**
- Credentials live only in the **encrypted vault**; the **LLM never sees a password or one-time code**. Code fills the fields.
- Dedicated Playwright persistent profile directory (separate from your personal browser), `0700`, wiped on request; cookies/session reused to avoid re-login; sessions expire.
- **Domain allowlist per provider** (navigation outside it is blocked at the context level); no popups/new origins; no file chooser; downloads only into a quarantine dir.
- Action allowlist: navigate, click on an allowlisted element, type into credential fields (code only), download. **Never** pay, change settings, or submit forms other than login; payment/account-settings URLs are in a deny list.
- **2FA / CAPTCHA / security questions: stop and ask the user** (headed mode: the user completes the step; the run resumes). Never try to bypass.
- Everything read from pages (text, DOM, screenshots) is **untrusted data** (S1); any LLM call about a page is tool-less and returns a strict schema.
- **Explicit user trigger only** ("Fetch bills" button), per provider, with a visible run log; never scheduled unattended while credentials are involved.
- Terms of service: automated access may be restricted by a provider; prefer the provider's own paperless-email or API/export options, and keep request rates human-like (one run, few pages).

**Implementation breakdown (cards R8-R10 below):** (1) `PortalRecipe` schema + vault storage (allowlist domains, steps, selectors, credential ref) ; (2) `PortalFetcher` (implements `ingest.PortalFetcher`) using Playwright with the constraints above; (3) quarantine-dir validation + hand-off to the ingestion pipeline; (4) per-provider recipes: electricity, gas, phone, each with a recorded-HTML fixture test; (5) failure UX (needs-you states).

## 6. Security requirements for ingestion and RAG (additions to platform_plan §2)

| # | Requirement | Enforced by (planned) |
|---|---|---|
| S-R1 | Downloaded/attached files are **untrusted**: accept only `application/pdf` by magic bytes `%PDF-`, size cap (e.g. 15 MB), page cap, no embedded executables/JS (reject PDFs with `/JavaScript`, `/Launch`, `/EmbeddedFile` actions), filename never used as a path (generate `<sha256>.pdf`), stored encrypted before any parsing, parsed in-process with pymupdf (no shelling out) | `ingest_files.validate_pdf`, tests with malformed/hostile PDFs |
| S-R2 | PDF text is untrusted: it enters the LLM only as `<untrusted>`-wrapped data in extraction (existing `data_extraction` call is tool-less); chunks sent to the answering model are wrapped and the system prompt states they are data; extraction output is schema-validated | `data_extraction`, `chat_engine` prompt, eval payloads in PDFs |
| S-R3 | Any new index structure (BM25/FTS, manifest, ledger) is either in the **encrypted vault** or documented plaintext-at-rest like `.chroma` (`security_design.md`); no plaintext copies of PDFs or text on disk, quarantine/download dirs are `0700` and emptied after ingestion | `security_design.md` entry per structure |
| S-R4 | Gmail: read-only scope (`gmail.readonly`), sender allowlist, attachments only, token in vault, no message-body text to prompts, no send/modify/delete tools (S6) | `ReadOnlyMcp`/API wrapper, tests |
| S-R5 | Browser agent constraints in §5 (credentials never to LLM, domain allowlist, no payments, 2FA = human, explicit trigger, untrusted page data) | recipe schema + Playwright context hooks + tests |
| S-R6 | Retrieval diagnostics/logs contain ids and scores only; never chunk text, never credentials | activity-log writer tests |
| S-R7 | Ingestion is idempotent and bounded: size/page/time caps, per-run file cap, no recursion into archives | pipeline tests |
| S-R8 | oMLX loopback-only applies to embeddings too (S14); document text never leaves the machine | existing |

## 7. Efficiency requirements

- Embeddings: **batch** (16-64 per request), **cache** by content hash, **incremental** (only new/changed chunks); target: re-opening the app with no new docs performs **0** embedding calls (today it re-embeds everything every session).
- Whole-document chunks for small bills reduce chunk count ~5-10x (10 → 1-2 for the sample bill) and embedding cost accordingly.
- Candidate budget: dense 20 + BM25 20 → rerank → 4-6 in prompt; prompt budget enforced; reranker only if it pays for itself in the eval.
- Query embedding cache (LRU) for repeated queries; metadata filters first (cheap) to shrink the vector search.
- Ingestion runs are bounded (files per run, bytes, seconds) and report progress; the app stays usable (run in a worker with progress, writes on the calling thread, S15).
- Rebuild is rare: only on `embed_model` or `chunker_version` change.

## 8. Executable plan (cards; do in order; each card ends with its Accept command green)

Conventions: tests in `tests/`, evals in `evals/` (`make eval-rag` is created in R1), keep `make test` and `make eval` green,
update `docs/platform_plan.md` §6 and `docs/security_design.md` in the same change. Estimates are agent time (minutes to hours), gated by review.

| Card | Goal | Files | Accept |
|---|---|---|---|
| **R0** | **Fix the chunk-per-line bug now** (smallest safe change): make `add_documents` call the structure-aware `chunk_text` (markdown-aware, merges short lines, overlap) with `chunk_chars` from the embedding model; min chunk 150 chars; unit tests (sample bill → 1-2 chunks; table kept whole; overlap only on split; hard cap respected). Bump `chunker_version` + collection name; rebuild index. | `ai_engine/chroma_store.py`, `tests/test_chroma_store.py` (also fixes B10) | `pytest tests/test_chroma_store.py`; sample bill chunk count <= 2 | **DONE 2026-10-25**: `_chunk_text` packs lines to the cap (overlap only on oversized-line splits, cap always respected), `add_documents` uses markdown-aware `_chunk_doc`, collection bumped to v4 (users must Rebuild RAG Index). Min-chunk-150 and per-model `chunk_chars` deferred to R1 measurement. |
| **R1** | **DONE 2026-10-25.** **Retrieval eval harness + baseline.** Synthetic bill generator, ~80 golden questions, metrics (§3.7), `make eval-rag`, record the **pre-fix baseline** and the R0 delta in `evals/results/rag_latest.md`. | `evals/rag/*`, `Makefile` | `make eval-rag` prints baseline + deltas | 44 synthetic docs (3 providers x 12 months + re-uploads, 2nd account, corrected bill, near-identical totals), 81 questions (lookup, field, comparison, aggregation, account, duplicate, corrected, 14 absent). Baseline frozen in `evals/rag/baseline.json` (legacy per-line chunks, bare metadata, category/provider filter only): recall@5 0.445, MRR 0.241, context precision 0.113, answer present in context 0.0, abstention 0.286. Hard gates on `final`: recall@5 >= 0.95, faithfulness >= 0.98, fabrication caught >= 0.98, abstention >= 0.95, answer-in-context >= 0.95, false abstain <= 0.05, stale/duplicate in context = 0. `final`: 1.0 / 1.0 / 0.985 / 1.0 / 1.0 / 0 / 0 / 0. `--sweep` shows coverage calibration. Also run by `tests/test_rag_eval.py`. |
| **R2** | **DONE.** **Contextual headers + richer metadata** (provider, category, period_start/end, title, page, doc_id, content hash) from extraction/upload metadata; header embedded and stored; period filter in `where`. | `chat_engine._rag_metadata_for`, `chroma_store.add_documents`, `query_analysis` | R1 context precision up; unit tests | Header `Provider \| Category \| Account \| Period \| Document` embedded+stored per chunk; metadata adds `period_start/end` (+ integer `period_*_n` for Chroma range filters; unknown = open-ended), `title`, `account_label`, `upload_timestamp`, `content_hash`; `build_rag_filter` period overlap; two-month questions span both. Context precision 0.146 -> 0.913, recall 0.56 -> 1.0 (this corpus states months explicitly, so the period filter is a big win here; real questions are vaguer). `page` is supported in the header but no parser emits page markers yet (parsers are ingestion-track code). |
| **R3** | **DONE.** **Batch + cached + incremental embeddings; version stamping.** `generate_embeddings(list)` in `OmlxClient`/`EmbeddingsGenerator`; embedding cache table in the vault; per-doc delete/re-add; collection metadata (`embed_model`, `dim`, `chunker_version`); stale-index detection → UI prompt; app open with no changes = 0 embed calls. | `omlx_client.py`, `embeddings.py`, `chroma_store.py`, `chat_engine.py` | tests count embed calls; `make eval-rag` unchanged or better | `OmlxClient.generate_embeddings` (one request per 32), `EmbeddingsGenerator.generate_embeddings` + `VaultEmbeddingCache`; embed calls to index 44 docs: 573 -> 44, HTTP calls 18 -> 2; second index of unchanged docs = 0 calls (tested). Collection stamped `embed_model`/`chunker_version` (`stale_reason()`, warning in Chat -> Advanced); dimension change fails closed; per-document `delete_document` + replace on re-add. NOTE: the Chroma client is in-memory today, so the index is rebuilt each session and staleness mainly matters if persistence is enabled; the cache is what makes startup cheap. |
| **R4** | **DONE (reranker/MMR not built).** **Qwen query instruction + hybrid retrieval + RRF**; calibrate `min_relevance`/`k` from R1; MMR; dedupe identical-hash docs. FTS/BM25 index inside the encrypted vault (or in-memory at unlock) and a `security_design.md` entry. | `embeddings.py`, new `ai_engine/hybrid.py`, `chroma_store.py` | `make eval-rag` recall@5 >= 0.95 | Qwen instruction on queries; dense top-20 + in-memory BM25 fused by RRF (k=60, same metadata filter); dedupe of identical `content_hash` and of superseded versions (same provider+period+account, account on both, newest upload wins); calibrated abstention = IDF-weighted query-term coverage of the best chunk (months/years/generic and comparison words excluded), plateau 0.2-0.9 on the eval corpus, default 0.5. Abstention 0.71 -> 1.0, stale and duplicate figures in context 1.0 -> 0. No reranker/MMR: no measured need on this corpus (precision 0.93). `min_relevance` (0.20) and `k` (5) were **not** re-tuned: the stand-in embedder cannot calibrate them for the real model; run `--sweep`-style calibration against live oMLX before changing them. |
| **R5** | **DONE.** **Context assembly v2** (grouped by document, evidence ids, period/page labels, ordered, budgeted, no mid-table truncation) + prompt v2 (quote + cite, abstain template, temp 0.1). | `rag_engine.get_context_for_query`, `chat_engine._finalize_prompt`, `config.py` | eval faithfulness up; prompt unit tests | One block per document `[Source n \| provider \| category \| period \| id=ev_xxxxxxxx]` (n = relevance rank, shown newest period first); budget drops whole highest-numbered blocks, an over-budget single block is cut at a line boundary; quote-and-cite prompt, templated abstain, no model call on empty retrieval, QA temperature 0.1 (restored afterwards). |
| **R6** | **DONE.** **Answer-time grounding check** (numeric/date/currency in answer must occur in cited context; citation exists) with visible "could not verify" note and one regenerate; log ids/scores only. | new `ai_engine/grounding.py`, `chat_engine.py` | `make eval-rag` faithfulness >= 0.98; fabricated-number tests fail closed | `grounding.check`: numbers/currency/dates normalised, checked against the *cited* block(s); missing citation on a figure sentence or unknown `[ev_id]` fails; a figure that is a sum/difference of <=15 context money figures is reported as `derived`, not failed. Non-stream: one regenerate naming the flagged claims, then a visible note; stream: note only. Runs on the labelled-block RAG path, not on the structured-records path. Per-turn ids/scores/flags (no text) -> encrypted `agent_activity_log` (`kind=rag_turn`). Faithfulness 1.0, fabricated figures caught 0.985 (a synthetic digit change that equals a sum of two context figures slips as `derived`). |
| **R7** | **Ingestion core:** manifest + expected-bills ledger (vault), `validate_pdf` (S-R1), idempotent incremental pipeline (hash dedupe, supersede, verify step, report), "Sync" panel in Upload/Dashboard (what is new/overdue/failed), headless `run_ingest()`. Verify Gmail attachment support (MCP vs Google API `gmail.readonly`) and decide. | new `data_ingestion/pipeline.py`, `ledger.py`, `interface/` panel | `pytest tests/test_ingest_pipeline.py` incl. hostile-PDF and duplicate tests; `make eval` **DONE (code+tests) 2026-10-25**: `data_ingestion/{files,pipeline}.py` (`validate_pdf`, manifest `ingest_manifest`, ledger `ingest_expectations`, `run_ingest`, supersede, per-run caps), `interface/sync_panel.py` on the Upload page. Gmail attachment support is **unverified against a live Gmail MCP server** (tool names are parameters). Verify step is a callback (default = text-layer present); the RAG self-retrieval smoke test and `chunker_version` stamping need Track A's R3 and are not wired. Superseded docs: `on_superseded` callback must delete their chunks. |
| **R8** | **Gmail attachment fetcher:** sender allowlist per provider, `has:attachment filename:pdf`, attachments only, read-only token in vault, feeds R7. | new `data_ingestion/gmail_attachments.py` | fixture-based tests; no body text reaches prompts **DONE (fixture-tested) 2026-10-25**: `data_ingestion/gmail_attachments.py` (code-side sender-domain check, PDF attachments only, bounded base64, bodies never read). Not run against live Gmail; default tool names `search_messages`/`get_attachment` are assumptions. No Sync-panel button yet (headless `run_ingest` only). |
| **R9** | **Portal recipe framework:** `PortalRecipe` schema (allowlisted domains, steps, selectors, credential ref), vault storage, Playwright runner implementing `PortalFetcher` with §5 constraints (persistent profile, deny list, download quarantine, human-in-the-loop for 2FA/CAPTCHA, explicit trigger, run log). Install `playwright` (public PyPI only; pin) and browsers locally. | new `data_ingestion/portal/*` | tests against local mock portal pages (login, 2FA step, bills list, hostile popup/redirect) **DONE 2026-10-25**: `data_ingestion/portal/{recipe,runner,store}.py` (closed action set, https+host allowlist, payment/settings deny list, `challenge_selectors` -> human, vault creds). `PlaywrightDriver` tested with real headless Chromium against mock pages (login, 2FA stop, bills, popup, meta-refresh redirect); `playwright==1.64.1` pinned, install browser with `playwright install chromium-headless-shell`. Headed 2FA flow (`wait_until_clear`) is **not** exercised by tests. |
| **R10** | **Provider recipes:** electricity, gas, phone (one at a time, recorded with `playwright codegen`, HTML fixtures, selectors repaired via B-mode with human approval). | `data_ingestion/portal/recipes/*` | per-provider fixture tests; one supervised live run by the user **PARTIAL 2026-10-25**: schema + `recipes.TEMPLATES` (electricity/gas/phone) target mock-portal fixtures only. **No real provider recipe exists**: record selectors with `playwright codegen` in a supervised live run, then `store.save_recipe` (re-validated). Live runs are the user's. |
| **R11** | **Docs + diagram:** update `ai_engine.md`, `platform_plan.md` §6/§6.1, `security_design.md`, Miro (RAG + ingestion frame), AGENTS.md rules. | docs | review |

Order rationale: R0 is a one-function bug fix with outsized effect; R1 makes every later change measurable; R2-R6 improve precision/faithfulness;
R7-R10 build freshness. R0-R6 are done; R7-R11 belong to the ingestion track. Do not tune thresholds before R1 exists.

## 8.1 Which RAG architecture? (Naive vs Advanced vs Modular)

| Pattern | Where personal_ai is / needs to be |
|---|---|
| **Naive** (index → retrieve → generate) | **Today's document path.** Cause of the accuracy problems in §2. Not enough. |
| **Advanced** (pre-retrieval rewrite → retrieve → post-retrieval rerank → generate) | **Needed for the document layer.** Pre-retrieval = query analysis to filters (category/provider/period) + Qwen instruction; retrieval = hybrid dense+BM25; post-retrieval = rerank/MMR/dedupe + context assembly + numeric grounding check (cards R2-R6). |
| **Modular** (routing, memory, fusion, orchestration with loops/conditionals) | **Already exists at the system level, deliberately not inside retrieval:** routing = structured-record vs semantic vs public-evidence paths; orchestration = Coordinator; fusion = deterministic `merge`; memory = approval-gated Core Memory. Retrieval-level loops stay bounded (at most one re-retrieve on a failed grounding check). |

Why not full Modular RAG for retrieval: free-running retrieve/reflect loops multiply local-model latency, add a prompt-injection surface and make behaviour harder to evaluate; the corpus is small and structured (bills), so deterministic routing plus filters beats an LLM deciding what to fetch.

## 9. Decisions

- RD6 Advanced RAG inside the document retriever; Modular behaviour only at the Coordinator level (see §8.1).

- RD1 One chunk per small document (header + whole text) beats line chunks for bills; chunk size is evaluated, not assumed.
- RD2 Deterministic browser recipes; the LLM may propose selector repairs, never credentials or actions at runtime.
- RD3 Gmail first for every provider that can email bills (one read-only path); portal agent only where necessary.
- RD4 Freshness is a visible ledger plus on-demand sync, because the laptop is not always on.
- RD5 Numbers are verified by code after generation; the model never gets the last word on a figure.
