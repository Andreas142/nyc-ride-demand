-- Every trip from every ingested service on one schema, with one flag per validity rule.
-- Nothing is dropped here: stg_trips applies the flags and mart_filter_audit counts them,
-- so every filter in the project is quantified.
-- Thresholds are deliberately loose; tighten them after the EDA and record why in
-- docs/decisions.md.

{% set services = var('services') %}

with unioned as (
    {% for service in services %}
    select '{{ service }}' as service, *
    from {{ source('bronze', service) }}
    {% if not loop.last %}union all by name{% endif %}
    {% endfor %}
)

-- A null in a checked field counts as a failure.
select
    *,
    -- TLC files contain a few records dated outside their month; keep each trip once.
    coalesce(strftime(pickup_at, '%Y-%m') <> source_month, true) as drop_wrong_month,
    coalesce(trip_seconds not between 60 and 4 * 3600, true) as drop_duration,
    coalesce(not (trip_miles > 0 and trip_miles < 100), true) as drop_distance,
    coalesce(fare < 0, true) as drop_fare
from unioned
