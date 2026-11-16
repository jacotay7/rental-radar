from pathlib import Path

from rental_radar.sources import alerts

FIXTURE = (Path(__file__).parent / "fixtures" / "zillow_alert.html").read_text()


def test_card_parser_reads_each_listing():
    recs = alerts.parse_alert(FIXTURE, "zillow", "Mon, 1 Jan 2030 10:00:00 -0600", "IL", filtered=True,
                              site_housing={"zillow": "house/townhouse"})
    assert [r["address"] for r in recs] == ["123 Maple Ave, Evanston, IL 60201", "45 Oak St, Chicago, IL"]
    first = recs[0]
    assert (first["price"], first["beds"], first["baths"], first["sqft"]) == (2150, 3, 2.0, 1400)
    assert first["url"] == "https://www.zillow.com/homedetails/111_zpid/"
    assert first["housing_type"] == "house/townhouse" and first["dogs_ok"] is True
    assert recs[1]["price"] == 1950


def test_per_room_pricing_is_skipped():
    html = "<div>$700/mo</div><div>4 bds | 2 ba</div><div>9 Elm St, Chicago, IL</div>"
    assert alerts.parse_cards(html, "zumper", "", "IL") == []


def test_generic_parser_fallback():
    html = "<p>New listing: 77 River Rd, Springfield, IL 62701 - $1,800/mo, 2 beds, 1 bath, 900 sqft</p>"
    recs = alerts.parse_alert(html, "alert", "", "IL")
    assert recs[0]["address"] == "77 River Rd, Springfield, IL 62701"
    assert (recs[0]["price"], recs[0]["beds"]) == (1800, 2)
