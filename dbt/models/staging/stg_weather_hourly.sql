-- One row per local hour (year files overlap slightly after the UTC to local conversion).

select
    hour_local as hour,
    avg(temp_c) as temp_c,
    avg(wind_ms) as wind_ms,
    max(precip_mm) as precip_mm
from {{ source('bronze', 'weather') }}
group by hour_local
