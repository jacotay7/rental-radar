import pytest

from rental_radar.config import DEFAULTS, deep_merge, finalize, load_areas

BASE = {
    "search": {"label": "Test", "state": "IL", "move_in": "2030-03-01",
               "bbox": {"south": 41.7, "north": 42.1, "west": -87.9, "east": -87.5}, "cities": []},
    "destination": {"label": "work", "address": "1 Test Plaza, Springfield, IL", "lat": 41.88, "lon": -87.64},
    "budget": {"target": 2000, "max": 2200, "hard_max": 2400},
    "size": {"min_beds": 1, "ideal_beds": 2},
    "pets": {"dog": True, "dog_weight_lb": 60},
    "email": {"to": ["someone@example.com"]},
}

AREAS = {
    "default_score": 5.5,
    "city_defaults": {"evanston": 8.0},
    "neighborhoods": [
        {"name": "Leafy Park", "lat": 41.90, "lon": -87.65, "radius_km": 1.0, "score": 9.0,
         "pros": "Quiet streets", "cons": "Pricey"},
        {"name": "Rough Edge", "lat": 41.80, "lon": -87.62, "radius_km": 1.0, "score": 4.0, "pros": "", "cons": ""},
    ],
    "transit_stations": [
        {"code": "AAA", "name": "Alpha", "lat": 41.901, "lon": -87.651, "rides": {"ZZZ": 10}},
        {"code": "ZZZ", "name": "Zulu", "lat": 41.879, "lon": -87.641, "rides": {}},
    ],
    "dog_parks": [{"name": "Bark Park", "lat": 41.902, "lon": -87.652}],
}


def make_cfg(**over):
    cfg = deep_merge(DEFAULTS, deep_merge(BASE, over))
    cfg["_dir"] = "."
    cfg["_data_dir"] = "./data"
    cfg["_path"] = "./config.yaml"
    cfg["_areas"] = deep_merge(load_areas(__import__("pathlib").Path("/nonexistent")), AREAS)
    return finalize(cfg)


@pytest.fixture
def cfg():
    return make_cfg()


def listing(**kw):
    base = {"uid": "test:1", "source": "test", "url": "https://example.com/1", "title": "Nice place",
            "price": 1900, "beds": 2, "baths": 1, "sqft": 1000, "lat": 41.90, "lon": -87.65,
            "address": "100 Main St, Chicago, IL 60601", "city": "Chicago", "housing_type": "apartment",
            "body": "", "dogs_ok": True, "contact": {}}
    base.update(kw)
    return base
