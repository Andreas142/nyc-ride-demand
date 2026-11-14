-- One row per valid trip across all ingested services.
-- The validity rules live in int_trips_flagged; their cost is in mart_filter_audit.

select
    service,
    provider,
    request_at,
    pickup_at,
    dropoff_at,
    date_trunc('hour', pickup_at) as pickup_hour,
    pu_zone_id,
    do_zone_id,
    trip_miles,
    trip_seconds,
    -- Seconds from request to pickup (Uber/Lyft only); implausible values become null.
    case
        when date_diff('second', request_at, pickup_at) between 0 and 3600
            then date_diff('second', request_at, pickup_at)
    end as wait_seconds,
    fare,
    tolls,
    tips,
    congestion_surcharge,
    airport_fee,
    -- The column only exists from January 2025; before that no fee was charged.
    coalesce(cbd_congestion_fee, 0) as cbd_congestion_fee,
    driver_pay,
    source_month
from {{ ref('int_trips_flagged') }}
where not (drop_wrong_month or drop_duration or drop_distance or drop_fare)
