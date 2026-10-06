PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
export PYTHONPATH := src
DB := data/warehouse.duckdb

.PHONY: setup data data-check demo accept live record practice ask test lint readme check

setup:       ## create .venv with DuckDB, sqlglot and the test tools
	python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"

data:        ## rebuild the made-up warehouse, its manifest and the answer key
	$(PYTHON) data/generate.py

$(DB): data/generate.py data/words.py
	$(PYTHON) data/generate.py --db

data-check:  ## confirm the warehouse and the answer key match the committed files
	$(PYTHON) data/generate.py --check

demo: $(DB)  ## three questions from the stored run, no model needed
	$(PYTHON) -m text_to_sql_agent demo

accept: $(DB)  ## the acceptance test on the stored run, no model needed
	$(PYTHON) -m text_to_sql_agent accept

live: $(DB)  ## the acceptance test live on Ollama, without storing the run
	$(PYTHON) -m text_to_sql_agent accept --live

record: $(DB)  ## run live on Ollama and store the run
	$(PYTHON) -m text_to_sql_agent accept --record

practice: $(DB)  ## live run on the practice split, for tuning
	$(PYTHON) -m text_to_sql_agent accept --split practice --live

ask: $(DB)   ## one question, live: make ask Q="How many orders came in last week?"
	$(PYTHON) -m text_to_sql_agent ask "$(Q)"

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .

readme:      ## refresh the results table in README.md from reports/
	$(PYTHON) -m text_to_sql_agent readme

check: lint test data-check accept  ## everything GitHub runs on each change
	$(PYTHON) -m text_to_sql_agent readme --check
