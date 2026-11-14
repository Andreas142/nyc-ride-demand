import math

import pandas as pd
import pytest

from nycdemand.ingest.weather import ghcnh_to_hourly, isd_to_hourly, isd_value


def test_isd_value_decodes_and_masks_missing():
    assert isd_value("+0228,1", 0) == pytest.approx(22.8)
    assert isd_value("-0051,1", 0) == pytest.approx(-5.1)
    assert math.isnan(isd_value("+9999,9", 0))
    assert isd_value("160,1,N,0046,1", 3) == pytest.approx(4.6)
    assert math.isnan(isd_value(None, 0))


def test_isd_converts_utc_to_new_york_hours():
    df = pd.DataFrame(
        {
            # 04:10 and 04:51 UTC on 1 July = 00:10 and 00:51 EDT -> one local hour
            "DATE": ["2025-07-01T04:10:00", "2025-07-01T04:51:00"],
            "TMP": ["+0220,1", "+0240,1"],
            "WND": ["180,1,N,0020,1", "180,1,N,0040,1"],
            "AA1": ["01,0005,9,5", "06,0100,9,5"],  # second is a 6-hour total, ignored
        }
    )
    hourly = isd_to_hourly(df)
    assert len(hourly) == 1
    row = hourly.iloc[0]
    assert row["hour_local"] == pd.Timestamp("2025-07-01 00:00:00")
    assert row["temp_c"] == pytest.approx(23.0)
    assert row["wind_ms"] == pytest.approx(3.0)
    assert row["precip_mm"] == pytest.approx(0.5)


def test_ghcnh_parses_and_converts():
    df = pd.DataFrame(
        {
            "Year": [2025, 2025],
            "Month": [1, 1],
            "Day": [6, 6],
            "Hour": [13, 14],
            "Minute": [51, 51],
            "temperature": [1.1, 2.2],
            "precipitation": [0.0, 0.3],
            "wind_speed": [4.1, 3.6],
        }
    )
    hourly = ghcnh_to_hourly(df)
    # 13:51 UTC in January = 08:51 EST
    assert hourly["hour_local"].tolist() == [
        pd.Timestamp("2025-01-06 08:00:00"),
        pd.Timestamp("2025-01-06 09:00:00"),
    ]
    assert (hourly["source"] == "ghcnh").all()


def test_ghcnh_missing_columns_raise_clear_error():
    with pytest.raises(ValueError, match="lacks"):
        ghcnh_to_hourly(pd.DataFrame({"Year": [2025]}))


def test_ghcnh_accepts_a_single_date_column():
    df = pd.DataFrame(
        {
            "DATE": ["2025-01-06T13:51:00", "not a date"],
            "temperature": [1.1, 2.2],
            "wind_speed": [4.1, 3.6],
        }
    )
    hourly = ghcnh_to_hourly(df)
    assert hourly["hour_local"].tolist() == [pd.Timestamp("2025-01-06 08:00:00")]
    assert hourly["precip_mm"].isna().all()
