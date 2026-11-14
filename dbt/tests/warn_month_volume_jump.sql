-- Warning only: trips more than 35% away from the previous built month of the same service.
-- Catches a half-published file; real shocks (holidays, strikes) show up here too.
{{ config(severity='warn') }}

with ordered as (
    select
        service,
        source_month,
        rows_kept,
        lag(rows_kept) over (partition by service order by source_month) as previous_rows
    from {{ ref('mart_filter_audit') }}
)

select *
from ordered
where previous_rows is not null
  and abs(rows_kept - previous_rows) > 0.35 * previous_rows
