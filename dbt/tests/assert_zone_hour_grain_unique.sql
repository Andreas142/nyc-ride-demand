-- Exactly one row per service, zone and hour.
select service, zone_id, hour, count(*) as n
from {{ ref('fct_zone_hour_demand') }}
group by service, zone_id, hour
having count(*) > 1
