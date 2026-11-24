.PHONY: setup lint format test check data workspace-plan workspace bronze-plan bronze dbt-deps dbt-parse dbt-build

# Local settings (not secrets) such as DBT_DATABRICKS_HOST; see .env.example.
-include .env
export

# dbt gets a short-lived OAuth token from your Databricks CLI login, so no
# long-lived access token is ever created or stored.
DBT_TOKEN = $$(databricks auth token --host "https://$$DBT_DATABRICKS_HOST" \
	| python -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')

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

bronze-plan:  ## Print the Bronze SQL (CREATE TABLE + COPY INTO); changes nothing
	python -m lakehouse.bronze --plan

bronze:  ## Load the landing CSVs into Bronze Delta tables and reconcile counts per tenant
	python -m lakehouse.bronze

dbt-deps:  ## Install dbt packages (dbt_utils)
	cd transform && dbt deps

dbt-parse:  ## Check the dbt project compiles; needs no Databricks connection (CI runs this)
	cd transform && DBT_DATABRICKS_HOST=ci DBT_DATABRICKS_HTTP_PATH=ci DBT_DATABRICKS_TOKEN=ci \
		dbt parse --profiles-dir .

dbt-build:  ## Build Silver and Gold on Databricks and run every dbt test, in dependency order
	cd transform && DBT_DATABRICKS_TOKEN="$(DBT_TOKEN)" dbt build --profiles-dir .
