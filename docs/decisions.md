# Decision log

Short records of choices that shape the project. Add one whenever a choice would surprise
a reviewer. Format: context, decision, consequence.

## ADR-001: DuckDB and dbt instead of Spark (2026-11-08)

- **Context:** the full range is hundreds of millions of trips, but the project only needs
  about 15 columns, and models train on zone-hour aggregates (roughly 2.3 million rows a
  year per service).
- **Decision:** DuckDB for ingestion and the warehouse, dbt for tested SQL transformations.
- **Consequence:** runs on a 2-core Codespace at zero cost. If a later stage outgrows one
  machine, the dbt models port to Spark or Databricks with small changes.

## ADR-002: slim bronze layer with one schema (2026-11-08)

- **Context:** raw monthly files are large and their schemas drift (column names, types,
  `cbd_congestion_fee` only from 2025).
- **Decision:** read each file once, keep only needed columns, map every service and year
  onto one schema, record each file in a manifest.
- **Consequence:** downstream SQL never handles schema drift; re-runs are idempotent.

## ADR-003: infer the Congestion Relief Zone from the data (2026-11-08)

- **Context:** the analysis needs to know which taxi zones are inside the charging zone.
- **Decision:** flag a zone when most trips that start and end inside it after 5 Jan 2025
  carry a `cbd_congestion_fee` (threshold 0.5, minimum 100 such trips).
- **Consequence:** no hand-maintained list. The flag is computed from `fct_od_day`, so it
  is rebuilt on every run. **To do in module 1:** compare the flagged zones with the MTA map
  (Manhattan south of and including 60th Street) and note any disagreements here.

## ADR-004: real data only (2026-11-08)

- **Decision:** every result in this repository comes from real TLC and NOAA data. No
  synthetic data, no simulated effects. Tests use a few hand-written rows for code paths.

## ADR-005: one backtest for every model, boosted or neural (2026-11-08)

- **Context:** tabular boosting usually wins on demand data like this, but deep learning is
  worth testing honestly, and reviewers want to see why a model was chosen.
- **Decision:** LightGBM is the main model. XGBoost and CatBoost (tuned with Optuna on the
  same budget), Chronos-2 zero-shot, N-HiTS, TFT and an own spatio-temporal graph model are
  challengers. All are scored on the same rolling folds, against the same seasonal
  baselines, for point error, range coverage and cost to train and serve.
- **Consequence:** one results table answers "which model, and is the extra complexity worth
  it". The champion-challenger gate in module 5 picks what serves; a neural model only
  replaces LightGBM if it wins on that table.

## ADR-006: CPU in Codespaces, GPU on Kaggle (2026-11-08)

- **Context:** Codespaces has no GPU and 15 GB-month of free storage; CUDA builds of PyTorch
  take several GB. Training N-HiTS, TFT and a graph model across 260 zones is slow on 2 cores.
- **Decision:** install CPU-only PyTorch in Codespaces (`make setup-dl`) for code, tests and
  small runs. Train neural models in Kaggle notebooks (free T4/P100 GPU), log runs to the
  same MLflow format and save model files back to the repo's release assets. Retrain
  neural models every few months; the boosted models retrain monthly in GitHub Actions.
- **Consequence:** no paid compute. Neural retraining is a manual step, documented in
  `docs/compute.md`. Chronos-2 runs zero-shot, so it needs no training at all.

## ADR-007: monthly aggregates, trip-level sample (2026-11-14)

- **Context:** slim trip-level files for the full range are estimated at around 10 GB,
  which does not fit the 15 GB-month of free Codespaces storage next to the environment.
  Every later module reads aggregates; only exploration and the quote model need single
  trips, and a sample is enough for both.
- **Decision:** build the marts one month at a time with dbt incremental models
  (`delete+insert`, keyed on the month). Once a month is in the marts, keep a 2% random
  sample of its trips (`data/bronze/<service>_sample/`) and delete the trip-level file. The
  newest built month stays whole. Marts store sums and counts wherever possible, so they
  re-aggregate exactly to zone groups, clusters and days. Every validity rule is a flag,
  and `mart_filter_audit` records what each rule drops per service and month.
- **Consequence:** any month can be rebuilt without touching the others, an interrupted
  backfill resumes, and a rebuilt month replaces itself. The cost: the warehouse is the
  only full copy of the history, so `--full-refresh` is refused on the monthly marts, and
  adding a column that needs trip-level data means `nycdemand backfill --rebuild` (a few
  hours of downloading, free). Module 5 publishes the marts so a lost codespace costs
  nothing. **To do in module 1:** record the measured sizes here after the backfill.
