.PHONY: setup lint format test check data workspace-plan workspace

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

workspace-plan:  ## Check the CSVs against the landing contract and print the plan; changes nothing
	python -m lakehouse.setup_workspace --dry-run

workspace:  ## Create catalog, schemas and landing volume, then upload the checked CSVs
	python -m lakehouse.setup_workspace
