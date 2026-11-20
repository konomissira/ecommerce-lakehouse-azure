.PHONY: setup lint format test check data

setup:  ## Install the package in editable mode with dev tools, and the git hooks
	python -m pip install -e ".[dev]"
	pre-commit install

lint:  ## Lint and check formatting without changing files
	ruff check .
	ruff format --check .

format:  ## Fix lint issues and format
	ruff check --fix .
	ruff format .

test:
	python -m pytest

check: lint test  ## What CI runs

data:  ## Generate the mock Shopify data (copied from the original project)
	cd data_generator && python generate_shopify_data.py
