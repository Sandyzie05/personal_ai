# Development Plan and Verification Gates

Assessment date: 2026-10-19. This is the current build plan, not a claim that
the full capstone is finished. The three coursework documents under `.idea/`
remain historical inputs; their present-tense descriptions do not establish
runtime capability.

## Goal and scope

Deliver the Week 1 personal assistant/forecaster: cited personal-document
answers, bounded public-market research, user-controlled persistence and
measurable task outcomes. Week 2 adds useful future directions, but automatic
delegation, Gmail digests, portal access, schedules and curated core memory
are not already implemented.

Keep the current single-user Streamlit app and existing encrypted vault.
Do not introduce a second backend, key lifecycle or orchestration framework.
Trading, payments, arbitrary shell execution, autonomous account access and
medical diagnosis are outside the research-only capstone.

## Where we are

| Capability | Source-level state | Remaining proof or work |
|---|---|---|
| Authentication and encrypted primary vault | Implemented; atomic POSIX-locked auth writes and session lock teardown | Backup/recovery and separate-session revocation remain outside this checkpoint |
| Upload, categories and structured extraction | Implemented; registry-driven picker, in-memory parsing, malformed inputs fail closed | No OCR; health XML recognizes only supported health-record types |
| Local document chat and conversation sessions | Implemented; saved settings, truthful idempotent rebuild and mutation refresh | Cross-tab transaction semantics and prompt-budget refinements |
| Configurable agents and MCP | Implemented tool loop and persisted confirmation pauses | Confirmation is configurable, not a universal research-only policy |
| Dashboard | Implemented structured-record aggregation | Not an agent-monitoring or scheduled-report system |
| Public requested-ticker research | Implemented fixed provider adapters and heuristic scores | Explicit coverage failures; no historical data-vintage reconstruction |
| Parallel forecast specialists | Implemented public-only one-shot reviews | No general coordinator or recursive delegation |
| Forecast/outcome saves | Implemented explicit, digest-bound encrypted immutable saves | Manual official-session attestation and supplied closing prices |
| Approved task memory and cross-agent coordination | Proposed | Typed contracts, policy enforcement, UI and adversarial tests |
| Gmail, portal fetching, schedules and holdings integration | Deferred | Separate consent, design and security review |

Implementation is not verification. Record actual commands/results in the
delivery PR; do not infer accuracy, calibration, safety or profitability
from code existence.

### Foundation work delivered in this checkpoint

The first implementation builds the safety baseline, not the whole
coordinator. Changes include synthetic offline diagnostics, network/DNS and
dotenv test isolation, saved backend/embedding settings, authenticated oMLX
streaming, private-log minimization, hashed audit arguments, atomic serialized
auth persistence, full lock teardown, bounded chunks and synchronized
rebuild/delete/reclassification retrieval. UI upload preparation/confirmation
no longer stages plaintext files; original bytes still use encrypted storage.
The picker reuses the handler's format registry, including DOCX/XML.
Embedding backend/host/model namespaces prevent dimension collisions;
source-only updates avoid rebuilding the vault for every uploaded file.
Regression targets include a real Chroma dimension-validator 2-to-3 switch
and exactly 100 embedding calls for 100 one-chunk uploads.

Real headless Streamlit registration, lock, wrong-password and correct-password
login are exercised with throwaway HOME/vaults. The local context guard reports
the configured 8,192-token window below the installed default model's
131,072-token limit.

Measured local snapshot (2026-10-19): `make test` passed 514 tests and 5
subtests, with 7 third-party SWIG deprecation warnings. Syntax parsing passed
all 94 Python files; links resolved in all 8 README/design documents;
`git diff --check` passed. Common token/private-key patterns had no matches
in 102 current source/test/doc files; this is not an exhaustive or historical
secret audit. Synthetic ambient API credentials were excluded by the test
harness. Final independent-review disposition and remote check results
belong to the delivery PR. Independent correctness review's two reproduced
retrieval issues were fixed and re-reviewed; final source security review
found no confirmed actionable regression. Neither review is a security
certification or dependency audit. No future milestone target below is a
measured result.

## Ordered implementation steps

Each checkpoint must be independently reviewable. Split changes into coherent
batches of at most five files; keep regression tests with the behavior changed.

### 1. Reconcile design and establish a safe baseline (current checkpoint)

1. Correct inaccurate encryption/egress claims, broken documentation links,
   unsupported API examples and model/backend defaults.
2. Replace live diagnostic tests and credential-like literals with mocked,
   synthetic-credential assertions. Isolate HOME and dotenv in UI tests.
3. Repair Chroma test doubles; tests must not download embedding models or
   contact configured services.
4. Honor saved inference and embedding settings, normalize missing config,
   and invalidate affected cached engines after Save.
5. Align backend capability checks and authenticated streaming behavior.
6. Remove private text from routine logs; address confirmed security findings
   with targeted regressions and update the residual-risk record.

**Gate:** targeted tests and the full offline suite pass using synthetic data;
changed Python parses; no real vault, auth file, credential store or provider
is used. Security findings have a fixed or explicit unresolved disposition.

### 2. Finish vault, lock, ingestion and retrieval lifecycle

1. Make auth-state writes atomic and permission-safe without changing the
   wrapping format; serialize concurrent state mutations.
2. Release all session-owned DEK-bearing clients, runtime/history references
   and pending upload state on lock. Do not promise Python memory zeroization.
3. Eliminate unnecessary plaintext upload staging; otherwise define an
   explicit private staging boundary with deterministic cleanup and failure
   behavior. Parser input must stay within the intended upload flow.
4. Give indexed chunks stable source identity; replacement, deletion and
   recategorization must update retrieval, not just vault records.
5. Make the rebuild action actually rebuild and report failures honestly.
6. Add transaction-backed updates where multiple tabs can lose index changes.

**Gate:** isolated register, lock, wrong/right login and lockout tests;
malformed/traversal uploads fail closed; upload, replace, recategorize,
delete and rebuild sequences never retrieve stale private content.
Interrupting an auth write preserves the last valid wrapped DEK.

### 3. Enforce typed research-only tool policy

1. Define narrow validated operations around existing retrieval, structured
   records, deterministic summaries, research and specialist review.
2. Deny arbitrary destinations, commands, trades and payments in this workflow.
   Keep generic user-configured MCP agents explicitly separate.
3. Construct public research packets from allowed public fields, never from
   raw private prompts, history, holdings or retrieved text.
4. Bind approvals to exact operation, payload, scope and run identity.
5. Treat tool results, uploaded text and model output as untrusted data.

**Gate:** adversarial synthetic cases cannot bypass scope, act while locked,
execute forbidden operations or send private canaries to public transports.
Unknown/invalid operations fail visibly without a success-shaped fallback.

### 4. Add bounded coordination

1. Introduce typed task/run state with explicit evidence and terminal status.
2. Route tasks only to permitted typed operations; preserve provenance.
3. Enforce shared model/tool/fan-out budgets across child work, at most five
   coordinator steps and one classified transient retry per failing operation.
4. Surface abstention, partial evidence, timeout and cancellation honestly.
   A deadline cannot promise to forcibly cancel synchronous network I/O.

**Gate:** at least 18/20 labeled synthetic tasks select the correct
evidence-bearing sequence; at least 9/10 labeled recoverable cases succeed;
unrecoverable cases terminate visibly. These numbers are targets until run.

### 5. Add approved encrypted task memory

1. Define preferences, corrections, task summaries and outcomes with
   provenance, approval, expiry and supersession.
2. Preview candidate memories; persist only the exact approved content.
3. Retrieve authorized memories by decrypting scoped records in memory.
   Do not add private memory to the plaintext Chroma index.
4. Implement corrections, expiry and deletion before automatic extraction.

**Gate:** at least 9/10 seeded relevant memories are retrieved; unapproved,
expired, deleted, superseded and inaccessible records never surface.
Changing a pending payload invalidates approval.

### 6. Integrate assistance and forecasting locally

1. Compose cited private obligation summaries with independently generated
   public research; join results locally.
2. Reuse existing evidence snapshots, specialists and immutable saves rather
   than building a second forecasting pipeline.
3. Preserve explicit session/price provenance and separate 5-/21-session
   evaluation. Missing prices must never become latest-quote substitutes.

**Gate:** mixed synthetic tasks retain citations and source dates, expose
provider failures and never leak private canaries. No investment-suitability,
calibration or historical-backtest claim without supporting evidence.

### 7. Evaluate, document and deliver

1. Run focused regressions, full offline tests, syntax and applicable existing
   lint/format checks; run `make check-context` for chat-path changes.
2. Review staged content for secrets and personal records without printing
   suspected values. A scanner is evidence, not a guarantee of no historical
   disclosure. Suspected live credentials require owner revocation/rotation.
3. Perform security review; document unresolved risks, prerequisites and
   measured acceptance results separately from future targets.
4. Create/update the task's GitHub PR, inspect all checks and paginated review
   discussions, and fix actionable findings before claiming readiness.
   No merge, deployment or force push.

**Gate:** reproducible green local checks, current docs, all applicable remote
checks/reviews resolved or an explicitly reported external blocker.

## Validation and data boundaries

Use existing `venv` tooling, temporary HOME/vaults and synthetic fixtures.
Inspect test entry points before executing; never use a real `.env`, vault,
OAuth token, personal upload or authenticated provider for routine regression
testing. Mock network and embedding transports; model downloads are not tests.
`make demo` and `make check-context` are live integration checks, not offline
tests; isolate their storage and report unavailable services explicitly.

Syntax-only validation requires no app import:

```bash
python3 -I -B -c 'import ast; from pathlib import Path; files=[*Path("src").rglob("*.py"),*Path("tests").rglob("*.py")]; [ast.parse(p.read_text(), filename=str(p)) for p in files]; print(f"Parsed {len(files)} files")'
```

The current checkpoint implements step 1 and selected lifecycle fixes in step
2; it does not silently mark every gate in steps 2-6 delivered. Their remaining
gates determine the next build, in order.

## Residual risks and deferred scope

See [security design](security_design.md) for the actual threat model and
plaintext index/skills limitations. Localhost UI binding does not enforce
local inference endpoints or prevent configured remote MCP egress.
Forgotten passwords have no recovery mechanism. Backup/key migration,
unattended schedules, account connectors, voice, sync, historical backtesting
and forecast calibration each require separate validated work.