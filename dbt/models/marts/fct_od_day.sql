-- Trips per service, pickup zone, drop-off zone and day (only pairs that had trips).
-- Used for flows into, within and out of the Congestion Relief Zone (module 3), for the
-- zone graph (module 2b) and for the congestion-zone flag in dim_zones.
-- Built one month at a time (ADR-007); all measures are additive.

{{ monthly_incremental(['service', 'pu_zone_id', 'do_zone_id', 'day']) }}

select
    service,
    pu_zone_id,
    do_zone_id,
    cast(pickup_hour as date) as day,
    count(*) as trips,
    count(*) filter (where cbd_congestion_fee > 0) as trips_with_cbd_fee,
    sum(trip_seconds) as sum_trip_seconds,
    sum(trip_miles) as sum_trip_miles,
    sum(fare) as sum_fare,
    sum(cbd_congestion_fee) as sum_cbd_fee
from {{ ref('stg_trips') }}
where source_month = '{{ month_var() }}'
  and pu_zone_id is not null
  and do_zone_id is not null
group by all
