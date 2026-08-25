#!/usr/bin/env python3
"""
One-command bootstrap for the Personal AI System.

Run via `make setup` (or directly: `python3 scripts/setup.py`). Does, in
order, everything the README's manual "Setup" section otherwise asks you to
do by hand:

  1. Checks the system Python version (3.10+ required).
  2. Creates ./venv and installs requirements.txt into it (same as
     `make install`).
  3. Checks the `ollama` CLI is installed.
  4. Checks the Ollama server is actually reachable.
  5. Pulls the configured chat + embedding models (src/config.py), skipping
     any that are already present locally.

Safe to re-run - every step is a no-op if already satisfied (existing venv
is reused, already-pulled models are skipped). Exits non-zero with a clear
message at the first step it can't complete on its own (e.g. Ollama isn't
installed, or isn't running) rather than pulling multi-GB models against a
server that isn't there.
"""

import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / "venv"
MIN_PYTHON = (3, 10)


def _print_step(step: str) -> None:
    print(f"\n==> {step}")


def check_python_version() -> bool:
    _print_step("Checking Python version")
    if sys.version_info < MIN_PYTHON:
        print(
            f"✗ Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, "
            f"found {sys.version_info.major}.{sys.version_info.minor}."
        )
        return False
    print(f"✓ Python {sys.version_info.major}.{sys.version_info.minor}")
    return True


def create_venv_and_install() -> bool:
    _print_step("Setting up ./venv and installing dependencies")
    venv_python = VENV_DIR / "bin" / "python3"
    if not venv_python.exists():
        print("Creating virtual environment at ./venv ...")
        result = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)])
        if result.returncode != 0:
            print("✗ Failed to create virtual environment.")
            return False
    else:
        print("✓ ./venv already exists, reusing it")

    venv_pip = VENV_DIR / "bin" / "pip"
    print("Installing requirements.txt ...")
    result = subprocess.run(
        [str(venv_pip), "install", "-r", "requirements.txt"],
        cwd=REPO_ROOT,
        env={**__import__("os").environ, "PIP_CONFIG_FILE": "pip.conf"},
    )
    if result.returncode != 0:
        print("✗ pip install failed - see output above.")
        return False
    print("✓ Dependencies installed")
    return True


def check_ollama_installed() -> bool:
    _print_step("Checking for the Ollama CLI")
    if shutil.which("ollama") is None:
        print(
            "✗ `ollama` was not found on your PATH.\n"
            "  Install it from https://ollama.com/download, then re-run this script."
        )
        return False
    print("✓ Ollama CLI found")
    return True


def check_ollama_running(host: str) -> bool:
    _print_step(f"Checking the Ollama server is reachable at {host}")
    try:
        urllib.request.urlopen(host, timeout=5)
    except urllib.error.URLError:
        print(
            f"✗ Could not reach Ollama at {host}.\n"
            "  Start it with `ollama serve` (or open the Ollama app), then "
            "re-run this script to pull models."
        )
        return False
    print("✓ Ollama is running")
    return True


def _normalize_tag(name: str) -> str:
    """`ollama list` always shows an explicit tag (e.g. `foo:latest`) even
    when the tag was omitted at pull time - strip a trailing `:latest` so
    "nomic-embed-text" (the config default, no tag) compares equal to
    "nomic-embed-text:latest" (what `ollama list` actually shows)."""
    return name[: -len(":latest")] if name.endswith(":latest") else name


def _installed_models() -> set:
    result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    if result.returncode != 0:
        return set()
    lines = result.stdout.strip().splitlines()[1:]  # skip header row
    return {_normalize_tag(line.split()[0]) for line in lines if line.strip()}


def pull_models(models: list) -> bool:
    _print_step("Pulling required Ollama models")
    installed = _installed_models()
    ok = True
    for model in models:
        if _normalize_tag(model) in installed:
            print(f"✓ {model} already pulled")
            continue
        print(f"Pulling {model} (this may take a while) ...")
        result = subprocess.run(["ollama", "pull", model])
        if result.returncode != 0:
            print(f"✗ Failed to pull {model}")
            ok = False
    return ok


def main() -> int:
    skip_models = "--skip-models" in sys.argv

    if not check_python_version():
        return 1
    if not create_venv_and_install():
        return 1
    if not check_ollama_installed():
        return 1

    sys.path.insert(0, str(REPO_ROOT))
    from src.config import DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL, DEFAULT_OLLAMA_HOST

    if not check_ollama_running(DEFAULT_OLLAMA_HOST):
        return 1

    if not skip_models:
        if not pull_models([DEFAULT_CHAT_MODEL, DEFAULT_EMBED_MODEL]):
            return 1
    else:
        print("\n==> Skipping model pull (--skip-models)")

    print(
        "\n✓ Setup complete. Start the app with:\n\n    make run\n\n"
        "On first run you'll be asked to set a vault password - see README.md."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
