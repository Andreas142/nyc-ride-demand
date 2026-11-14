-- Demand per service, pickup zone and local hour on a complete grid: hours with no trips
-- are 0, not missing, which forecasting needs.
--
-- Built one month at a time (ADR-007). Measures are stored as sums and counts wherever
-- possible, so zones and hours can be re-aggregated exactly later (zone groups, clusters,
-- days); medians cannot be re-aggregated and are kept for the hourly grain only.
-- Join dim_zones for zone attributes: nothing that can change is copied here.

{{ monthly_incremental(['service', 'zone_id', 'hour']) }}

{% set month_start = "cast('" ~ month_var() ~ "-01' as timestamp)" %}

with hours as (
    -- From the calendar, not from the data: a month with a missing day keeps every hour.
    select unnest(generate_series(
        {{ month_start }},
        {{ month_start }} + interval 1 month - interval 1 hour,
        interval 1 hour
    )) as hour
),

grid as (
    select s.service, z.zone_id, h.hour
    from (select unnest({{ var('services') }}) as service) s
    cross join (select zone_id from {{ ref('stg_zones') }} where is_mappable) z
    cross join hours h
),

agg as (
    select
        service,
        pu_zone_id as zone_id,
        pickup_hour as hour,
        count(*) as trips,
        sum(trip_seconds) as sum_trip_seconds,
        sum(trip_miles) as sum_trip_miles,
        sum(fare) as sum_fare,
        sum(driver_pay) as sum_driver_pay,
        sum(cbd_congestion_fee) as sum_cbd_fee,
        count(*) filter (where cbd_congestion_fee > 0) as trips_with_cbd_fee,
        count(wait_seconds) as trips_with_wait,
        sum(wait_seconds) as sum_wait_seconds,
        median(fare) as median_fare,
        median(trip_seconds) as median_trip_seconds
    from {{ ref('stg_trips') }}
    where source_month = '{{ month_var() }}'
    group by all
)

select
    g.service,
    g.zone_id,
    g.hour,
    coalesce(a.trips, 0) as trips,
    coalesce(a.sum_trip_seconds, 0) as sum_trip_seconds,
    coalesce(a.sum_trip_miles, 0) as sum_trip_miles,
    coalesce(a.sum_fare, 0) as sum_fare,
    a.sum_driver_pay,
    coalesce(a.sum_cbd_fee, 0) as sum_cbd_fee,
    coalesce(a.trips_with_cbd_fee, 0) as trips_with_cbd_fee,
    coalesce(a.trips_with_wait, 0) as trips_with_wait,
    a.sum_wait_seconds,
    a.median_fare,
    a.median_trip_seconds
from grid g
left join agg a using (service, zone_id, hour)
