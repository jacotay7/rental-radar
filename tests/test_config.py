import textwrap

import pytest

from rental_radar.config import load_config, validate
from tests.conftest import make_cfg


def test_example_configs_are_valid():
    for name in ("east-bay", "minimal"):
        cfg = load_config(f"examples/{name}/config.yaml")
        assert validate(cfg) == [], name


def test_defaults_fill_in_and_bool_shorthand(tmp_path):
    (tmp_path / "config.yaml").write_text(textwrap.dedent("""
        search: {bbox: {south: 1, north: 2, west: 1, east: 2}}
        destination: {lat: 1.5, lon: 1.5}
        budget: {target: 1000, max: 1100, hard_max: 1200}
        sources: {craigslist: false, realtor: false}
    """))
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg["sources"]["craigslist"] == {"enabled": False, "site": None, "postal": None, "distance_mi": 20}
    assert cfg["sources"]["redfin"]["enabled"] is True
    assert cfg["scoring"]["email_threshold"] == 72
    assert cfg["budget"]["great"] == 875
    assert cfg["_data_dir"] == str(tmp_path / "data")
    assert validate(cfg) == []


def test_validate_catches_problems():
    cfg = make_cfg(budget={"target": 3000, "max": 2000, "hard_max": 2400})
    cfg["destination"]["lat"] = None
    cfg["sources"]["craigslist"]["enabled"] = True
    errs = "\n".join(validate(cfg))
    assert "destination.lat" in errs
    assert "target <= max <= hard_max" in errs
    assert "sources.craigslist" in errs


def test_missing_config_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="radar init"):
        load_config(tmp_path / "nope.yaml")
