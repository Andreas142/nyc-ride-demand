-- Taxi zones, plus a Congestion Relief Zone flag inferred from the data itself.
--
-- Rule: a zone is inside the zone when most trips that start and end in it (after the fee
-- launched) carry a cbd_congestion_fee. Trips that both start and end outside the zone are
-- rarely charged, so the split is sharp. Validate the result against the MTA map (Manhattan
-- south of and including 60th Street) and record the check in docs/decisions.md (ADR-003).
--
-- Computed from fct_od_day, so it is rebuilt on every run and survives the pruning of
-- trip-level files. Until a month from January 2025 onwards is built, no zone is flagged.

with intra_zone_trips as (
    select
        pu_zone_id as zone_id,
        sum(trips) as intra_trips,
        sum(trips_with_cbd_fee) / sum(trips) as cbd_fee_share
    from {{ ref('fct_od_day') }}
    where pu_zone_id = do_zone_id
      and day >= cast('{{ var("cbd_fee_start") }}' as date)
    group by all
)

select
    z.zone_id,
    z.borough,
    z.zone_name,
    z.service_zone,
    z.is_mappable,
    coalesce(i.intra_trips, 0) as intra_trips_post_launch,
    i.cbd_fee_share,
    coalesce(i.cbd_fee_share > 0.5 and i.intra_trips >= 100, false) as in_congestion_zone
from {{ ref('stg_zones') }} z
left join intra_zone_trips i using (zone_id)
