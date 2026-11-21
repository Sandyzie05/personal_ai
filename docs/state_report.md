# State report (second laptop → development session)

The models run on a second laptop; development happens on another. After each user-testing session on the model laptop, a local
coding agent (opencode) produces **one redacted markdown report** that the user copies and pastes into the development
session. **Paste only:** never commit, push, gist or upload it, and do not save it inside the repo.

This repo is **public** and the model laptop holds the real vault. The report is metadata only.

## 1. Hard rules (apply to every line)

**Never include:** document or chat text; model answers (quote at most the *shape*, e.g. "gave August total for a September
question"); provider/biller names; amounts; bill dates/periods; account numbers or suffixes; email addresses or senders; held
tickers or position sizes (tickers you typed for a *public research test* are allowed); `env`, `.env`, API keys, tokens, passwords;
vault/Chroma file contents; exception *messages* (type + file:line only); absolute paths (use `~/...`).

**Do not** unlock the vault, read `~/.personal_ai_vault`, `~/.personal_ai_auth` or the Chroma directory, or ask for the password.
Vault numbers come from the user reading the UI (counts only).

**Self-check before printing:** run the scanner below on the draft and drop every flagged line; then re-read for names/amounts.

```bash
PYTHONPATH=. python3 -c "import sys; from src.agents.orchestration.bus import PII_PATTERNS as P; t=open(sys.argv[1]).read(); [print(k, m) for k,r in P.items() for m in r.findall(t)]" /tmp/state_report.md
```

## 2. Commands (run from the repo root; record only the fields in §3)

| Field | Command |
|---|---|
| Code | `git rev-parse --abbrev-ref HEAD`, `git rev-parse --short HEAD`, `git status --porcelain \| wc -l` |
| Runtime | `python3 --version`; `pip show streamlit chromadb pydantic mcp playwright 2>/dev/null \| grep -E '^(Name\|Version)'` |
| Machine | `sysctl -n hw.memsize` (GB), `sw_vers -productVersion`, `memory_pressure -Q \| tail -1` |
| oMLX | `curl -s -m 5 http://127.0.0.1:8000/v1/models` (add `-H "Authorization: Bearer $OMLX_API_KEY"` if needed; **never print the key**); report model ids only |
| Config | non-secret values of `PERSONAL_AI_LLM_ENGINE`, `PERSONAL_AI_OMLX_CHAT_MODEL`, `PERSONAL_AI_OMLX_EMBED_MODEL`, `PERSONAL_AI_OMLX_WORKER_MODEL`, `PERSONAL_AI_INFERENCE_CONCURRENCY` (print each by name; never dump `env`) |
| oMLX memory | `ps -o rss= -p $(pgrep -f -n omlx)` before and after the session (GB) |
| Tests | `make test 2>&1 \| tail -1` |
| Evals (synthetic, safe to paste) | `make eval` → table from `evals/results/latest.md`; `make eval-rag` → `evals/results/rag_latest.md`; `make eval-live` if oMLX is up |
| Context | `make check-context` (last 5 lines) |

## 3. Report template (fill exactly this; leave a field `n/a` rather than guess)

```markdown
# State report: <host alias, e.g. "model-laptop">, <YYYY-MM-DD HH:MM>
## Code
branch: … | commit: … | uncommitted files: N | python: … | streamlit/chromadb/pydantic/mcp/playwright: …
## Machine and engine
RAM: N GB | macOS: … | memory pressure: … | oMLX reachable: yes/no | models loaded: …
chat/embed/worker model: … | inference concurrency: N | oMLX RSS before/after: N / N GB
## Automated checks
make test: <summary line> | check-context: ok/fail (reason)
make eval: Overall PASS/FAIL; failing rows: …   (paste the table)
make eval-rag: PASS/FAIL; failing gates: …
make eval-live: PASS/FAIL/not run; pass rate: …
## User testing (docs/user_testing_guide.md)
| Step id (A1…E6) | Pass/Fail/Skip | Symptom (shape only, no data) | Latency s | Notes |
## Live measurements (roadmap P9)
RAG question: N s | Coordinator cross-domain: N s, steps N, escalations kinds: … | ToT converged ticker: N s, nodes N, LLM calls N |
ToT depth-2 ticker: N s, nodes N, LLM calls N | peak oMLX RSS: N GB | memory pressure peak: …
## Vault (from the UI, counts only)
documents per category: … | Rebuild RAG Index: ok/fail, N s | stale-index warning: yes/no | Core Memory pending/approved: N/N
## Errors seen
| Where (page/command) | Exception type | file:line | Reproducible? |
## Questions / blockers for the dev session
- …
```
