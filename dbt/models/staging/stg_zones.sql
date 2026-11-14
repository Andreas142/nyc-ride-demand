-- Official TLC taxi zones. Zones 264-265 are "unknown" / outside NYC and are not mappable.

select
    cast(LocationID as integer) as zone_id,
    Borough as borough,
    Zone as zone_name,
    service_zone,
    Borough not in ('Unknown', 'N/A') and cast(LocationID as integer) <= 263 as is_mappable
from {{ source('reference', 'zones') }}
