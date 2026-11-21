"""Redacted state report for the model laptop (docs/state_report.md). Metadata only: never reads the vault,
.env or env dumps, never prints secrets. Prints markdown to stdout for the user to paste.

Usage: PYTHONPATH=. venv/bin/python scripts/state_report.py [--quick]   (or `make state-report`)"""

import importlib.metadata as md
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ["streamlit", "chromadb", "pydantic", "mcp", "playwright", "httpx", "pytest"]
HOME = str(Path.home())


def sh(*cmd, timeout=600):
    try:
        p = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:  # report the type only: messages can carry data
        return -1, type(e).__name__


def versions():
    out = []
    for name in PACKAGES:
        try:
            out.append(f"{name} {md.version(name)}")
        except md.PackageNotFoundError:
            out.append(f"{name} MISSING")
    return ", ".join(out)


def omlx(cfg):
    key = os.environ.get("omlx_api_key") or os.environ.get("OMLX_API_KEY")
    req = urllib.request.Request(cfg.PERSONAL_AI_OMLX_HOST.rstrip("/") + "/v1/models")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    t = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            ids = [m.get("id", "?") for m in json.load(r).get("data", [])]
            return f"yes (HTTP {r.status}, {time.monotonic() - t:.2f} s)", ", ".join(
                ids
            ) or "none loaded"
    except urllib.error.HTTPError as e:
        hint = " - set OMLX_API_KEY in this shell" if e.code in (401, 403) else ""
        return f"no (HTTP {e.code}{hint})", "n/a"
    except urllib.error.URLError as e:  # e.g. ConnectionRefusedError = nothing listening on that port
        return f"no ({type(e.reason).__name__})", "n/a"
    except Exception as e:
        return f"no ({type(e).__name__})", "n/a"


def omlx_rss_gb():
    code, pids = sh("pgrep", "-f", "omlx")
    if code != 0 or not pids:
        return "no oMLX process found"
    _, rss = sh("ps", "-o", "rss=", "-p", ",".join(pids.split()))
    return f"{sum(int(x) for x in rss.split() if x.isdigit()) / 1048576:.1f} GB"


def results_table(path):
    p = ROOT / path
    if not p.exists():
        return "n/a (no results file)"
    lines = p.read_text().splitlines()
    overall = next((l for l in lines if "Overall" in l), "Overall: n/a")
    failing = [
        l for l in lines if l.startswith("|") and l.rstrip().endswith("| FAIL |")
    ]
    return overall.strip("* ") + (
        "; failing rows:\n" + "\n".join(failing) if failing else "; failing rows: none"
    )


def main(quick: bool) -> str:
    sys.path.insert(0, str(ROOT))
    from src import config as c
    from src.agents.orchestration.bus import PII_PATTERNS

    _, branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD")
    _, commit = sh("git", "rev-parse", "--short", "HEAD")
    _, changed = sh("git", "diff", "--name-only", "HEAD")
    _, untracked = sh("git", "ls-files", "--others", "--exclude-standard")
    _, mem = sh("sysctl", "-n", "hw.memsize")
    _, free = sh("memory_pressure", "-Q")
    free_pct = (re.search(r"(\d+)%", free) or [None, "n/a"])[1]
    reach, models = omlx(c)
    venv = (
        "project venv"
        if sys.prefix != sys.base_prefix
        else "NOT the project venv: use venv/bin/python"
    )

    checks = ["(skipped: --quick)"]
    if not quick:
        code, out = sh("make", "test")
        test_line = next(
            (
                l
                for l in reversed(out.splitlines())
                if " passed" in l or " failed" in l or "error" in l.lower()
            ),
            out[-200:],
        )
        sh("make", "eval")
        sh("make", "eval-rag")
        _, ctx = sh("make", "check-context")
        checks = [
            f"make test (exit {code}): {test_line.strip('= ')}",
            f"make eval: {results_table('evals/results/latest.md')}",
            f"make eval-rag: {results_table('evals/results/rag_latest.md')}",
            f"make check-context: {(ctx.splitlines() or ['n/a'])[-1]}",
        ]
        if (
            "evals/results" not in changed
        ):  # evals rewrite tracked result files; leave the tree as found
            sh("git", "checkout", "--", "evals/results")

    report = (
        f"""# State report: model-laptop, {datetime.now().strftime("%Y-%m-%d %H:%M")}
## Code
branch: {branch} | commit: {commit} | modified tracked files: {changed.replace(chr(10), ", ") or "none"} | untracked files: {len(untracked.splitlines())}
python: {platform.python_version()} ({venv}) | {versions()}
## Machine and engine
RAM: {int(mem) // 2**30 if mem.isdigit() else "n/a"} GB | macOS: {platform.mac_ver()[0]} | memory free: {free_pct}% (memory_pressure -Q) | oMLX RSS: {omlx_rss_gb()}
oMLX ({c.PERSONAL_AI_OMLX_HOST}) reachable: {reach} | API key in this shell: {"yes" if os.environ.get("omlx_api_key") or os.environ.get("OMLX_API_KEY") else "no"}
models served: {models}
config (env/defaults; the Settings page can override): engine={c.PERSONAL_AI_LLM_ENGINE}, embed_engine={c.PERSONAL_AI_EMBED_ENGINE}, chat={c.PERSONAL_AI_OMLX_CHAT_MODEL}, embed={c.PERSONAL_AI_OMLX_EMBED_MODEL}, worker={c.PERSONAL_AI_OMLX_WORKER_MODEL or "(= chat)"}, concurrency={c.PERSONAL_AI_INFERENCE_CONCURRENCY}, context_window={c.DEFAULT_CONTEXT_WINDOW_TOKENS}, ollama_enabled={c.OLLAMA_ENABLED}
## Automated checks
"""
        + "\n".join(checks)
        + """
## User testing (docs/user_testing_guide.md) - filled from the user's answers
| Step id | Pass/Fail/Skip | Symptom (shape only, no data) | Latency s | Notes |
|---|---|---|---|---|
<ROWS>
## Live measurements (roadmap P9) - from the user
<LIVE>
## Vault (from the UI, counts only) - from the user
<VAULT>
## Errors seen (exception type + file:line only)
<ERRORS>
## Questions / blockers
<QUESTIONS>
"""
    )
    report = report.replace(HOME, "~")
    flagged = [
        l
        for l in report.splitlines()
        if any(p.search(l) for p in PII_PATTERNS.values())
    ]
    for line in flagged:
        report = report.replace(line, "[line removed by PII self-check]")
    return report


if __name__ == "__main__":
    print(main("--quick" in sys.argv))
