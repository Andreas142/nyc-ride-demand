# Notebooks

Exploration only. Anything a later step depends on moves into `src/` or `dbt/` with a test.

Read the warehouse with `duckdb.connect("data/warehouse.duckdb", read_only=True)`. Single
trips live in the 2% samples: `read_parquet("data/bronze/*_sample/*.parquet")`.

Planned:

- `00_first_number.ipynb`: the seasonal-naive forecast error on the sample months, as a
  first end-to-end check that the data is forecastable at the zone-hour grain.
- `01_eda.ipynb`: trip volumes by service and month, data-quality checks, outliers that
  should tighten the validity rules in `int_trips_flagged`, and the congestion zone flag
  check (ADR-003).
