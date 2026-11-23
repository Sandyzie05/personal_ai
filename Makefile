.PHONY: help setup install run demo test check-context install-skills eval eval-live eval-rag eval-stability state-report obs-up obs-down run-obs

VENV_BIN := $(shell [ -x venv/bin/python3 ] && echo venv/bin)
PYTHON := $(if $(VENV_BIN),$(VENV_BIN)/python3,python3)
STREAMLIT := $(if $(VENV_BIN),$(VENV_BIN)/streamlit,streamlit)
PYTEST := $(if $(VENV_BIN),$(VENV_BIN)/pytest,pytest)

help:
	@echo "Personal AI System - available targets:"
	@echo "  make setup          One-command bootstrap: venv, deps, Ollama check, pull models"
	@echo "  make obs-up / obs-down  Local OpenTelemetry stack (Grafana otel-lgtm, loopback only)"
	@echo "  make run-obs        Run the app with telemetry export enabled"
	@echo "  make install        Create ./venv and install Python dependencies into it"
	@echo "  make run            Start the Streamlit web interface"
	@echo "  make demo           Run the CLI ai_engine demo (registers/logs in via AuthManager)"
	@echo "  make check-context  Validate context-window/token-budget assumptions against Ollama"
	@echo "                      Optional: FILE=path/to/text.txt  MODEL=some-model"
	@echo "  make test           Run isolated offline tests (synthetic loopback callback allowed)"
	@echo "  make eval-live      Opt-in: injection corpus against the live local model (needs oMLX running)"
	@echo "  make eval           Run the offline eval harness (evals/results/latest.md); non-zero on target miss"
	@echo "  make state-report Redacted state report to paste into the dev session (QUICK=1 skips tests/evals)"
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
	PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PYTEST) tests/ -v

eval:
	PYTHONPATH=. $(PYTHON) -m evals.run

state-report:
	@PYTHONPATH=. $(PYTHON) scripts/state_report.py $(if $(QUICK),--quick)

eval-rag:
	PYTHONPATH=. $(PYTHON) -m evals.rag.run

install-skills:
	PYTHONPATH=. $(PYTHON) -c "from src.agents.orchestration.skill_pack import install_builtin_skills as i; print(i())"

eval-live:
	PYTHONPATH=. $(PYTHON) -m evals.live

RUNS ?= 10
eval-stability:
	PYTHONPATH=. $(PYTHON) -m evals.stability --runs $(RUNS) $(if $(LIVE),--live,)

# Track O: local OpenTelemetry stack (Grafana otel-lgtm, loopback ports only; data deleted by obs-down)
# Ports default to 3000/4318; if taken (e.g. a local Jaeger on 4318): OBS_OTLP_PORT=14318 make obs-up run-obs
OBS_GRAFANA_PORT ?= 3000
OBS_OTLP_PORT ?= 4318
export OBS_GRAFANA_PORT OBS_OTLP_PORT

obs-up:
	@lsof -nP -iTCP:$(OBS_OTLP_PORT) -sTCP:LISTEN >/dev/null 2>&1 && { echo "Port $(OBS_OTLP_PORT) is in use (see: lsof -i :$(OBS_OTLP_PORT)). Stop that process or pick a free port, e.g. OBS_OTLP_PORT=14318 make obs-up run-obs"; exit 1; } || true
	@lsof -nP -iTCP:$(OBS_GRAFANA_PORT) -sTCP:LISTEN >/dev/null 2>&1 && { echo "Port $(OBS_GRAFANA_PORT) is in use (another Grafana?). Pick a free one, e.g. OBS_GRAFANA_PORT=3001 make obs-up"; exit 1; } || true
	docker compose -f observability/docker-compose.yml up -d
	@echo "Grafana: http://127.0.0.1:$(OBS_GRAFANA_PORT) (folder personal_ai). Now run: OBS_OTLP_PORT=$(OBS_OTLP_PORT) make run-obs"

obs-down:
	docker compose -f observability/docker-compose.yml down

run-obs:
	PERSONAL_AI_OTEL_ENABLED=true PERSONAL_AI_OTEL_ENDPOINT=http://127.0.0.1:$(OBS_OTLP_PORT) PYTHONPATH=. $(STREAMLIT) run src/interface/main.py
