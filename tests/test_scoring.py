from datetime import date

from rental_radar import analyze, geo
from tests.conftest import listing, make_cfg


def score(d, cfg, ff=10):
    return analyze.analyze(d, ff, cfg)


def test_price_curve_is_monotonic(cfg):
    pros, cons = [], []
    vals = [analyze.price_score({"price": p}, cfg, pros, cons) for p in range(1500, 2401, 50)]
    assert vals == sorted(vals, reverse=True)
    assert vals[0] == 1.0


def test_dog_weight_limit_is_a_dealbreaker(cfg):
    ok, _ = score(listing(body="Dogs welcome."), cfg)
    bad, a = score(listing(body="Dogs under 40 lbs only."), cfg)
    assert bad <= cfg["scoring"]["email_threshold"] - 10 < ok
    assert any("weight limit 40" in c for c in a["cons"])


def test_no_pets_is_a_dealbreaker(cfg):
    s, a = score(listing(body="Tenant pays utilities. No pets, sorry."), cfg)
    assert s <= cfg["scoring"]["email_threshold"] - 10


def test_pets_component_dropped_without_pets():
    cfg = make_cfg(pets={"dog": False, "cat": False})
    _, a = score(listing(body="No pets."), cfg)
    assert "pets" not in a["components"]


def test_neighborhood_dropped_without_ratings():
    cfg = make_cfg()
    cfg["_areas"] = dict(cfg["_areas"], neighborhoods=[], city_defaults={})
    _, a = score(listing(), cfg)
    assert "neighborhood" not in a["components"]
    assert not any("rated" in c for c in a["cons"])


def test_shared_fenced_yard_is_not_private(cfg):
    pros, cons = [], []
    assert analyze.yard_score({}, "huge shared fenced yard", pros, cons) == 0.55
    assert analyze.yard_score({}, "private fenced backyard", pros, cons) == 1.0


def test_walkup_penalty_and_house_exemption(cfg):
    cons = []
    assert analyze.stairs_penalty("3rd floor walk-up", {"housing_type": "apartment"}, cfg, cons) == 8
    assert analyze.stairs_penalty("3rd floor walk-up", {"housing_type": "house"}, cfg, cons) == 0
    assert analyze.stairs_penalty("2nd floor unit", {"housing_type": "flat"}, cfg, cons) == 4


def test_availability_infers_nearest_year():
    ref = date(2030, 1, 10)
    assert analyze.available_date({}, "available dec 20", ref) == date(2029, 12, 20)
    assert analyze.available_date({}, "available feb 1", ref) == date(2030, 2, 1)
    assert analyze.available_date({"available_text": "available 2030-03-05"}, "", ref) == date(2030, 3, 5)


def test_availability_bonus_and_penalty(cfg):
    pros, cons = [], []
    assert analyze.availability({}, "available mar 1", cfg, pros, cons) == 5
    assert analyze.availability({}, "available jun 1", cfg, pros, cons) == -8


def test_avoid_and_prefer_areas():
    base, _ = score(listing(), make_cfg())
    worse, _ = score(listing(), make_cfg(scoring={"avoid_areas": ["leafy"]}))
    better, _ = score(listing(), make_cfg(scoring={"prefer_areas": ["Leafy Park"]}))
    assert worse < base < better


def test_scam_phrases_cap_the_score(cfg):
    s, a = score(listing(body="I am currently abroad, keys will be mailed after you wire the deposit."), cfg)
    assert s <= 40 and a["scam_level"] == "likely"


def test_rent_far_below_home_value_is_likely_scam(cfg):
    d = listing(housing_type="house", price=2000, estimated_value=1_500_000)
    s, a = score(d, cfg)
    assert a["scam_level"] == "likely" and s <= 40


def test_below_min_beds_capped(cfg):
    s, _ = score(listing(beds=0), cfg)
    assert s <= 30


def test_weights_normalize():
    a, _ = score(listing(), make_cfg())
    doubled = make_cfg()
    doubled["scoring"]["weights"] = {k: v * 2 for k, v in doubled["scoring"]["weights"].items()}
    b, _ = score(listing(), doubled)
    assert abs(a - b) < 0.01


def test_neighborhood_lookup(cfg):
    areas = cfg["_areas"]
    assert geo.neighborhood(41.90, -87.65, "Chicago", areas)["name"] == "Leafy Park"
    assert geo.neighborhood(41.912, -87.65, "Chicago", areas)["name"] == "near Leafy Park"
    assert geo.neighborhood(42.05, -87.68, "Evanston", areas)["score"] == 8.0
    assert geo.neighborhood(42.05, -87.68, "Elsewhere", areas)["score"] == 5.5


def test_transit_commute():
    cfg = make_cfg(commute={"transit": {"enabled": True, "name": "L",
                                         "exits": {"ZZZ": {"name": "Zulu", "last_mile_minutes": 4}}}})
    t = geo.transit_commute(41.902, -87.652, cfg)
    assert t["station"] == "Alpha" and t["access"] == "walk" and t["exit"] == "Zulu"
    assert 20 <= t["minutes"] <= 25
