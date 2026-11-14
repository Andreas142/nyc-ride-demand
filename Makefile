# Run everything from the repository root.
.RECIPEPREFIX := >
.PHONY: help setup setup-ml setup-dl lint test sample backfill dbt-build status clean

DBT := dbt --no-version-check
DBT_ARGS := --project-dir dbt --profiles-dir dbt

help:            ## list targets
> @grep -E '^[a-z-]+:.*##' Makefile | sed 's/:.*##/ -/'

setup:           ## install the package, dev tools and notebook tools
> python -m pip install -e ".[dev,eda]"

setup-ml:        ## module 2-5 tools: LightGBM, XGBoost, CatBoost, Optuna, MLflow, Evidently, causal libraries
> python -m pip install -e ".[ml,causal]"

setup-dl:        ## module 2b tools: CPU-only PyTorch (keeps Codespaces storage small), neuralforecast, Chronos
> python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
> python -m pip install -e ".[dl]"

lint:            ## ruff lint and format check
> ruff check src tests
> ruff format --check src tests

test:            ## unit tests
> pytest -q

sample:          ## zones, weather, and Dec 2024 - Feb 2025 built into the marts (keeps the trip files)
> nycdemand ingest-zones
> nycdemand ingest-weather
> nycdemand backfill --start 2024-12 --end 2025-02 --no-prune

backfill:        ## every month in configs/project.yaml: ingest, build, prune older trip files (hours)
> nycdemand ingest-zones
> nycdemand ingest-weather
> nycdemand backfill

dbt-build:       ## rebuild and test one month: make dbt-build MONTH=2025-03
> @test -n "$(MONTH)" || (echo "Usage: make dbt-build MONTH=YYYY-MM" && exit 1)
> $(DBT) build $(DBT_ARGS) --vars '{month: "$(MONTH)"}'

status:          ## months in bronze, in the marts, and pruned to a sample
> nycdemand status

clean:           ## remove dbt artefacts (never the warehouse: after pruning it is the only full copy)
> rm -rf dbt/target dbt/logs
