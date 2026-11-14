# Data sources

All data is real and public. Nothing is generated or simulated; unit tests use a few
hand-written rows only to exercise code paths.

## NYC TLC trip records

- **What:** one row per trip for yellow taxis, green taxis and high-volume for-hire services
  (Uber, Lyft). Monthly Parquet files.
- **Where:** <https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page>
  Files: `https://d37ci6vzurychx.cloudfront.net/trip-data/<service>_tripdata_<YYYY-MM>.parquet`
- **Freshness:** published monthly with roughly a 1-3 month lag. Months that are not out
  yet are skipped, not errors, and are picked up by the next `make backfill`.
- **Scale:** about 3 million yellow trips a month; high-volume FHV ran at roughly 21-22
  million trips a month in December 2024 and December 2025 (TLC license pause report).
- **Key field for this project:** `cbd_congestion_fee`, added from 2025 onwards to the yellow,
  green and high-volume FHV data; it is the per-trip charge for MTA's Congestion Relief Zone
  starting 5 January 2025.
- **Data dictionaries:** yellow, green and high-volume FHV dictionaries are linked from the
  TLC page above (versions dated 18 March 2025 include `cbd_congestion_fee`).
- **Caveats:** TLC publishes the records as submitted and makes no representation about
  their accuracy. Files occasionally contain trips dated outside their month; those rows
  are dropped and counted in `mart_filter_audit`, like every other validity rule.
- **Terms:** NYC.gov terms of use.

## Taxi zone lookup

- `https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv`
  (LocationID, Borough, Zone, service_zone). Zones 264-265 are unknown / outside NYC and are
  excluded from the demand grid.
- The Congestion Relief Zone flag is inferred from the data in `dim_zones` (see
  `docs/decisions.md`, ADR-003) and checked against the MTA map.

## Weather (NOAA)

- Station: NY City Central Park (GHCN `USW00094728`; ISD `72505394728`).
- Primary: GHCN-hourly (GHCNh), by-year files under
  `https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/access/by-year/<YYYY>/`
  (`psv/GHCNh_<station>_<YYYY>.psv`; the ingest also tries the path without `psv/`, and
  accepts both the Year/Month/Day/Hour layout and the single `DATE` column layout).
- Fallback: ISD global-hourly `https://www.ncei.noaa.gov/data/global-hourly/access/<YYYY>/<station>.csv`.
- Converted from UTC to New York local time to join with trip timestamps. Precipitation
  periods vary between reports; see the caveat at the top of `src/nycdemand/ingest/weather.py`.
