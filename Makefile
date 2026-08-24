.PHONY: help install run demo test check-context

help:
	@echo "Personal AI System - available targets:"
	@echo "  make install        Install Python dependencies"
	@echo "  make run            Start the Streamlit web interface"
	@echo "  make demo           Run the CLI ai_engine demo (registers/logs in via AuthManager)"
	@echo "  make check-context  Validate context-window/token-budget assumptions against Ollama"
	@echo "                      Optional: FILE=path/to/text.txt  MODEL=some-model"
	@echo "  make test           Run the test suite"

install:
	PIP_CONFIG_FILE=pip.conf pip install -r requirements.txt

run:
	PYTHONPATH=. streamlit run src/interface/main.py

demo:
	PYTHONPATH=. python3 examples/ai_engine_demo.py

check-context:
	PYTHONPATH=. python3 scripts/check_context_overflow.py $(if $(FILE),--file $(FILE)) $(if $(MODEL),--model $(MODEL))

test:
	PYTHONPATH=. python3 test_metadata_fix.py
