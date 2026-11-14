import pytest

from nycdemand.config import load_config, month_range, parse_month
from tests.conftest import REPO_ROOT


def test_month_range_crosses_year():
    assert month_range("2024-11", "2025-02") == ["2024-11", "2024-12", "2025-01", "2025-02"]


def test_month_range_single_month():
    assert month_range("2025-01", "2025-01") == ["2025-01"]


def test_month_range_rejects_reversed():
    with pytest.raises(ValueError):
        month_range("2025-02", "2025-01")


def test_parse_month_rejects_bad_format():
    with pytest.raises(ValueError):
        parse_month("2025/01")


def test_repo_config_loads():
    cfg = load_config(REPO_ROOT / "configs" / "project.yaml")
    assert set(cfg.services) <= {"yellow", "green", "fhvhv"}
    assert cfg.months[0] == cfg.start_month
    assert cfg.months[-1] == cfg.end_month
