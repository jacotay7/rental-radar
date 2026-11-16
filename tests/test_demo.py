from rental_radar import demo


def test_demo_renders_offline(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(requests.Session, "request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    out = demo.run(tmp_path / "demo.html", open_browser=False, chat=True)
    html = out.read_text()
    assert "Demo:" in html and "likely scam" in html and "Albany" in html
    cfg, scored, items, hidden = demo.build()
    assert 3 <= len(items) < len(scored)
