-- A trip that ended before the Congestion Relief Zone charge started cannot carry it.
-- Trips picked up late on 4 Jan 2025 that end after midnight are charged legitimately,
-- so the rule is on drop-off time. Real data has a handful of exceptions (4 yellow trips
-- in Jan 2025): those warn and are recorded in the findings; more than 100 fails the build.
{{ config(severity='error', warn_if='>0', error_if='>100') }}

select *
from {{ ref('stg_trips') }}
where dropoff_at < cast('{{ var("cbd_fee_start") }}' as timestamp)
  and cbd_congestion_fee > 0
