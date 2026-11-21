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

## 2. Generate it (do not hand-collect)

```bash
git pull && venv/bin/pip install -q -r requirements.txt   # once per pull: the report checks the venv, not system python
make state-report            # full: also runs make test, eval, eval-rag, check-context (~2 min)
make state-report QUICK=1    # machine/engine/config only
```

`scripts/state_report.py` measures everything machine-readable the same way every time: packages from the **project venv**,
oMLX reachability through the app's own host and `OMLX_API_KEY` (HTTP status or error type, never the key), effective config
from `src/config.py` (env + defaults; the Settings page can override), free memory, oMLX RSS, eval result rows. It redacts
home paths, runs the bus PII scanner on its own output, and restores `evals/results/` afterwards.

The agent only replaces the `<ROWS>`, `<LIVE>`, `<VAULT>`, `<ERRORS>`, `<QUESTIONS>` placeholders with what the user reports,
then re-reads the whole report against §1. The script loads `.env` like the app does, so keys there count. If oMLX shows `no (HTTP 401 ...)`, the key in `.env`/env is wrong or missing; if
`no (ConnectionRefusedError)`, nothing listens on the configured host/port.

## 3. Report template (reference: what the script prints)

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
