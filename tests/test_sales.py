import sqlite3

from rental_radar import db, sales


def test_street_key():
    assert sales.street_key("123 Main Street, Springfield, IL 62701") == "123 main st"
    assert sales.street_key("2 Oak Way Unit 106, Town, CA") == "2 oak way #106"
    assert sales.street_key("Main Street") is None


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    return conn


DESC = ("Welcome to this stunning craftsman bungalow with a chef's kitchen, original built-ins, a sunny "
        "south-facing garden and a detached studio perfect for a home office, steps from shops and parks.")


def test_copied_description_from_another_address():
    conn = _conn()
    conn.execute("INSERT INTO sale_listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("p1", "916 elm st", "916 Elm St, Town, IL", 41.9, -87.6, "SOLD", 1_400_000, 1_450_000,
                  "2030-01-01", "2030-02-01", None, "Some Realty", DESC, db.now(), db.now()))
    rental = {"address": "3916 Elm St, Town, IL", "lat": 41.9, "lon": -87.6, "body": "Rent now! " + DESC}
    idx = sales.TextIndex(conn, [rental["body"]])
    m = sales.match(conn, rental, idx)
    assert m["copied_from"]["address"] == "916 Elm St, Town, IL"
    assert m["copied_from"]["same_address"] is False


def test_address_match_respects_units():
    conn = _conn()
    conn.execute("INSERT INTO sale_listings (property_id, street_key, address, status, first_seen, last_seen) "
                 "VALUES ('p2', '2 oak way #106', '2 Oak Way Unit 106', 'FOR_SALE', ?, ?)", (db.now(), db.now()))
    assert sales.match(conn, {"address": "2 Oak Way Unit 106, Town, IL"})["status"] == "FOR_SALE"
    assert sales.match(conn, {"address": "2 Oak Way Unit 107, Town, IL"}) is None
