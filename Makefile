.PHONY: help setup install run demo test check-context install-skills eval eval-live eval-rag

VENV_BIN := $(shell [ -x venv/bin/python3 ] && echo venv/bin)
PYTHON := $(if $(VENV_BIN),$(VENV_BIN)/python3,python3)
STREAMLIT := $(if $(VENV_BIN),$(VENV_BIN)/streamlit,streamlit)
PYTEST := $(if $(VENV_BIN),$(VENV_BIN)/pytest,pytest)

help:
	@echo "Personal AI System - available targets:"
	@echo "  make setup          One-command bootstrap: venv, deps, Ollama check, pull models"
	@echo "  make install        Create ./venv and install Python dependencies into it"
	@echo "  make run            Start the Streamlit web interface"
	@echo "  make demo           Run the CLI ai_engine demo (registers/logs in via AuthManager)"
	@echo "  make check-context  Validate context-window/token-budget assumptions against Ollama"
	@echo "                      Optional: FILE=path/to/text.txt  MODEL=some-model"
	@echo "  make test           Run the test suite"
	@echo "  make eval-live      Opt-in: injection corpus against the live local model (needs oMLX running)"
	@echo "  make eval           Run the offline eval harness (evals/results/latest.md); non-zero on target miss"
	@echo "  make eval-rag       Run the RAG retrieval/grounding eval (evals/results/rag_latest.md); non-zero on gate miss"

setup:
	python3 scripts/setup.py

install:
	python3 -m venv venv
	PIP_CONFIG_FILE=pip.conf venv/bin/pip install -r requirements.txt

run:
	PYTHONPATH=. $(STREAMLIT) run src/interface/main.py

demo:
	PYTHONPATH=. $(PYTHON) examples/ai_engine_demo.py

check-context:
	PYTHONPATH=. $(PYTHON) scripts/check_context_overflow.py $(if $(FILE),--file $(FILE)) $(if $(MODEL),--model $(MODEL))

test:
	PYTHONPATH=. $(PYTEST) tests/ -v

eval:
	PYTHONPATH=. $(PYTHON) -m evals.run

eval-rag:
	PYTHONPATH=. $(PYTHON) -m evals.rag.run

install-skills:
	PYTHONPATH=. $(PYTHON) -c "from src.agents.orchestration.skill_pack import install_builtin_skills as i; print(i())"

eval-live:
	PYTHONPATH=. $(PYTHON) -m evals.live
