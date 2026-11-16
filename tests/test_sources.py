"""Source parsers against hand-built samples in the shape each site returns. No network.

The samples are synthetic (invented addresses, example.com links): they pin down what the parsers expect,
so a change in a site's format shows up as a small, obvious fixture edit.
"""
import sys
import types
from types import SimpleNamespace as NS

import pytest

from rental_radar.sources import Incomplete, craigslist, portals, realtor, redfin
from tests.conftest import make_cfg


class Resp:
    def __init__(self, status=200, text="", data=None, url="https://x.example/"):
        self.status_code, self.text, self._data, self.url = status, text, data, url
        self.ok = 200 <= status < 300

    def json(self):
        return self._data

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


# ---------------------------------------------------------------- Craigslist

CL_DECODE = {"minPostingId": 7900000000, "minPostedDate": 1790000000, "locations": [[0, "x", "chc"]]}
CL_ITEM = [123, 456, 0, 1850, "0:1~41.9000~-87.6500", [6, "sunny-2br-near-park"], [5, 2, 900],
           [4, "3:00a0a_abc", "3:00b0b_def"], "Sunny 2BR near the park"]
CL_PAGE = """
<span id="titletextonly">Sunny 2BR near the park</span>
<div class="mapaddress">100 Main St</div>
<div id="map" data-latitude="41.9001" data-longitude="-87.6502"></div>
<span class="attr">2BR / 1.5Ba</span><span class="attr">house</span>
<span class="attr">available mar 1</span><span class="attr">dogs are OK - wooof</span>
<section id="postingbody">QR Code Link to This Post<br>Fenced yard.<br>Call (555) 010-0199 or
write demo@example.com. Apply at https://example.com/apply</section>
"""


def test_craigslist_search_item():
    cfg = make_cfg(sources={"craigslist": {"enabled": True, "site": "chicago", "postal": "60601"}})
    rec = craigslist._parse_item(CL_ITEM, CL_DECODE, cfg)
    assert rec["uid"] == "craigslist:7900000123" and rec["price"] == 1850 and rec["beds"] == 2
    assert rec["sqft"] == 900 and (rec["lat"], rec["lon"]) == (41.9, -87.65)
    assert rec["url"] == "https://chicago.craigslist.org/chc/apa/d/sunny-2br-near-park/7900000123.html"
    assert rec["photos"][0] == "https://images.craigslist.org/00a0a_abc_600x450.jpg"


def test_craigslist_splits_price_bands_at_the_result_cap(monkeypatch):
    cfg = make_cfg(sources={"craigslist": {"enabled": True, "site": "chicago", "postal": "60601"}})
    calls = []

    def fake_query(cfg, lo, hi):
        calls.append((lo, hi))
        n = craigslist.PAGE_CAP if (lo, hi) == (100, 2400) else 1
        return {"decode": CL_DECODE, "items": [[lo + i, 1, 0, lo, "0:1~41.9~-87.65"] for i in range(n)]}

    monkeypatch.setattr(craigslist, "_query", fake_query)
    found = craigslist.search(cfg)
    assert calls == [(100, 2400), (1251, 2400), (100, 1250)] and len(found) == 2


def test_craigslist_detail_page(monkeypatch):
    monkeypatch.setattr(craigslist.http, "get", lambda *a, **k: Resp(text=CL_PAGE))
    out = craigslist.fetch_detail({"url": "https://chicago.craigslist.org/x", "contact": {}})
    assert out["address"] == "100 Main St" and out["housing_type"] == "house" and out["baths"] == 1.5
    assert out["dogs_ok"] and not out["cats_ok"] and out["available_text"] == "available mar 1"
    assert out["body"].startswith("Fenced yard.") and (out["lat"], out["lon"]) == (41.9001, -87.6502)
    assert out["contact"]["phone"] == "(555) 010-0199" and out["contact"]["email"] == "demo@example.com"


def test_craigslist_deleted_post_is_gone_but_a_block_page_is_an_error(monkeypatch):
    monkeypatch.setattr(craigslist.http, "get", lambda *a, **k: Resp(404))
    assert craigslist.fetch_detail({"url": "u", "contact": {}}) is None
    monkeypatch.setattr(craigslist.http, "get", lambda *a, **k: Resp(403, "<html>blocked</html>"))
    with pytest.raises(RuntimeError, match="403"):
        craigslist.fetch_detail({"url": "u", "contact": {}})


# ---------------------------------------------------------------- Redfin

def _home(rid="r1", beds=(1, 3), price=(1500, 2300)):
    return {"homeData": {"url": "/IL/Chicago/100-Main-St/home/1", "propertyType": 6,
                         "addressInfo": {"formattedStreetLine": "100 Main St", "city": "Chicago", "state": "IL",
                                         "zip": "60601", "centroid": {"centroid": {"latitude": 41.9, "longitude": -87.65}}}},
            "rentalExtension": {"rentalId": rid, "rentPriceRange": {"min": price[0], "max": price[1]},
                                "bedRange": {"min": beds[0], "max": beds[1]}, "sqftRange": {"max": 1400},
                                "bathRange": {"max": 2}, "description": "Nice.", "numAvailableUnits": 3,
                                "feedOriginalSource": "TRBO", "desktopPhone": "5550100123"}}


def test_redfin_parse():
    rec = redfin._parse(_home(), make_cfg())
    assert rec["uid"] == "redfin:r1" and rec["price"] == 1500 and rec["price_max"] == 2300 and rec["beds"] == 3
    assert rec["housing_type"] == "house" and rec["address"] == "100 Main St, Chicago, IL 60601"
    assert rec["url"] == "https://www.redfin.com/IL/Chicago/100-Main-St/home/1" and rec["feed"] == "TRBO"


def test_redfin_tiles_the_box_at_the_result_cap(monkeypatch):
    cfg = make_cfg()
    calls = []

    def fake_query(cfg, s, n, w, e):
        calls.append((s, n, w, e))
        return [_home(f"r{len(calls)}-{i}") for i in range(redfin.PAGE_CAP if len(calls) == 1 else 2)]

    monkeypatch.setattr(redfin, "_query", fake_query)
    assert len(redfin.search(cfg)) == 8 and len(calls) == 5      # full box, then four quarters


def test_redfin_detail_picks_the_best_fitting_unit(monkeypatch):
    plans = {"unitTypesByBedroom": [{"availableUnitTypes": [{"units": [
        {"bedrooms": 1, "rentPrice": 1500, "sqft": 600, "name": "A"},
        {"bedrooms": 2, "rentPrice": 2100, "sqft": 950, "fullBaths": 1, "halfBaths": 1, "name": "B"},
        {"bedrooms": 3, "rentPrice": 3900, "sqft": 1300, "name": "C"}]}]}]}       # over hard_max

    def get(url, **k):
        return Resp(data={"unitAmenities": ["Dishwasher"]} if url.endswith("amenities") else plans)

    monkeypatch.setattr(redfin.http, "get", get)
    out = redfin.fetch_detail(redfin._parse(_home(), make_cfg()), make_cfg())
    assert (out["unit_name"], out["price"], out["beds"], out["baths"]) == ("B", 2100, 2, 1.5)
    assert out["amenities"] == ["Dishwasher"]


def test_redfin_detail_rate_limit_is_an_error(monkeypatch):
    monkeypatch.setattr(redfin.http, "get", lambda url, **k: Resp(429))
    with pytest.raises(RuntimeError, match="429"):
        redfin.fetch_detail(redfin._parse(_home(), make_cfg()), make_cfg())


# ---------------------------------------------------------------- Realtor.com (HomeHarvest objects)

def _prop(pid="p1", price=2000, beds=3, dogs=True):
    return NS(property_id=pid, property_url=f"https://example.com/{pid}", list_price=price, list_price_min=None,
              units=None, details=[{"category": "Rental", "text": ["Availability Date: 2030-03-05"]}], tags=["garage_1"],
              pet_policy=NS(dogs=dogs, dogs_large=dogs, cats=False), latitude=41.9, longitude=-87.65,
              description=NS(beds=beds, sqft=1500, baths_full=2, baths_half=1, text="Lovely home.", name=None,
                             style="SINGLE_FAMILY", primary_photo="https://example.com/p.jpg"),
              address=NS(full_line="100 Main St", city="Chicago", zip="60601"), photos=[], mls="MRED",
              estimated_value=450000, list_date=None,
              advertisers=NS(agent=NS(name="Demo Agent", email=None, phones=[{"number": "5550100111"}]),
                             office=NS(name="Demo Realty", email="office@example.com", phones=[])))


def test_realtor_parse():
    rec = realtor._parse(_prop(), make_cfg())
    assert rec["uid"] == "realtor:p1" and rec["housing_type"] == "house" and rec["baths"] == 2.5
    assert rec["dogs_ok"] and "Large dogs allowed." in rec["body"] and rec["available_text"] == "available mar 05"
    assert rec["contact"]["name"] == "Demo Agent / Demo Realty" and rec["contact"]["phone"] == "5550100111"
    assert rec["estimated_value"] == 450000 and rec["address"] == "100 Main St, Chicago, IL 60601"
    assert realtor._parse(_prop(price=9999), make_cfg()) is None            # over hard_max
    assert "no dogs" in realtor._parse(_prop(dogs=False), make_cfg())["body"]


def _fake_homeharvest(monkeypatch, fail=()):
    def scrape_property(location, **kw):
        if location.split(",")[0] in fail:
            raise RuntimeError("boom")
        return [_prop(pid=location.split(",")[0])]

    monkeypatch.setitem(sys.modules, "homeharvest", types.SimpleNamespace(scrape_property=scrape_property))
    monkeypatch.setattr(realtor.time, "sleep", lambda s: None)


def test_realtor_partial_failure_is_reported_as_incomplete(monkeypatch):
    cfg = make_cfg(search={"cities": ["Chicago", "Evanston"]})
    _fake_homeharvest(monkeypatch)
    found = realtor.search(cfg)
    assert len(found) == 2 and not isinstance(found, Incomplete)
    _fake_homeharvest(monkeypatch, fail=("Evanston",))
    found = realtor.search(cfg)
    assert len(found) == 1 and isinstance(found, Incomplete) and found.unknown_prefixes is None
    _fake_homeharvest(monkeypatch, fail=("Chicago", "Evanston"))
    with pytest.raises(RuntimeError, match="every city"):
        realtor.search(cfg)


# ---------------------------------------------------------------- property-manager portals

APPFOLIO = """<script>var x = { markers: [{"detail_page_url": "/listings/detail/abc-123", "address":
"100 Main St, Chicago, IL 60601", "unit_specs": "3 bd / 2 ba / 1,500 Sq. Ft.", "rent_range": "$2,100",
"latitude": 41.9, "longitude": -87.65, "default_photo_url": "https://example.com/a.jpg"}],
 other: 1 };</script>
<div class="listing-item result js-listing-item">
 <h2 class="js-listing-title"> <a href="/listings/detail/abc-123">Bright house with yard</a></h2>
 <dd class="js-listing-available">3/1/30</dd>
 <p><span class="js-listing-pet-policy">Dogs allowed, Cats allowed</span> </p>
</div>"""
BUILDIUM = """<a class="featured-listing" href="/Resident/public/rentals/9001"><div data-rent="1900"
 data-bedrooms="2" data-bathrooms="1.5" data-square-feet="1100" data-type="SingleFamily"></div>
 <img src="/img/9001.jpg"><h3 class="featured-listing__title">200 Oak St</h3>
 <p class="featured-listing__address">Chicago, IL 60601</p>
 <p class="featured-listing__availability"> Available now </p>
 <p class="featured-listing__description">Cozy place.<div></div></p></a>"""
RENTVINE = [{"unit": {"unitID": 77, "address": "300 Elm St", "city": "Chicago", "postalCode": "60601", "rent": "2000",
                      "beds": "3", "baths": "2", "size": "1400", "latitude": "41.9", "longitude": "-87.65"},
             "property": {"propertyTypeID": "1"},
             "listing": {"headline": "Elm St home", "description": "Yard.", "acceptDogs": "0", "phone": "5550100122"}}]


def _portal_get(url, **k):
    if "appfolio" in url:
        return Resp(text=APPFOLIO)
    if "managebuilding" in url:
        return Resp(text=BUILDIUM, url=url)
    if "broken" in url:
        return Resp(500)
    return Resp(data=RENTVINE)


def test_portals_parse_all_three_platforms(monkeypatch, tmp_path):
    (tmp_path / "portals.yaml").write_text(
        "portals:\n  - {name: A, platform: appfolio, sub: aaa}\n  - {name: B, platform: buildium, sub: bbb}\n"
        "  - {name: C, platform: rentvine, sub: ccc}\n")
    cfg = make_cfg()
    cfg["_dir"] = str(tmp_path)
    monkeypatch.setattr(portals.http, "get", _portal_get)
    monkeypatch.setattr(portals, "geocode", lambda conn, addr: (41.9, -87.65))
    by = {d["platform"]: d for d in portals.search(cfg, None)}
    a, b, c = by["appfolio"], by["buildium"], by["rentvine"]
    assert (a["uid"], a["price"], a["beds"], a["sqft"]) == ("pm:aaa:abc-123", 2100, 3, 1500)
    assert a["dogs_ok"] and a["title"] == "Bright house with yard" and a["available_text"] == "available 3/1/30"
    assert (b["price"], b["beds"], b["baths"], b["housing_type"]) == (1900, 2, 1.5, "house")
    assert b["address"] == "200 Oak St, Chicago, IL 60601" and b["available_text"] == "available now"
    assert c["housing_type"] == "house" and "pet policy: no dogs" in c["body"] and not c["dogs_ok"]
    assert c["address"] == "300 Elm St, Chicago, IL 60601"


def test_a_failing_portal_keeps_its_listings(monkeypatch, tmp_path):
    (tmp_path / "portals.yaml").write_text(
        "portals:\n  - {name: A, platform: appfolio, sub: aaa}\n  - {name: X, platform: rentvine, sub: broken}\n")
    cfg = make_cfg()
    cfg["_dir"] = str(tmp_path)
    monkeypatch.setattr(portals.http, "get", _portal_get)
    found = portals.search(cfg, None)
    assert isinstance(found, Incomplete) and found.unknown_prefixes == ("pm:broken:",) and len(found) == 1
