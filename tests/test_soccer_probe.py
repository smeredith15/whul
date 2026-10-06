"""The richer soccer sources' probe: it must say what it lacks, not fail."""

from whul.sources import soccer_probe


def test_without_a_key_the_paid_api_says_how_to_get_one(monkeypatch):
    monkeypatch.delenv("API_FOOTBALL_KEY", raising=False)
    found = soccer_probe.probe_api_football((2024,))
    assert found["status"].startswith("NO KEY")


def test_a_refusing_host_is_reported_not_raised(monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr(soccer_probe.requests, "get", refuse)
    monkeypatch.setattr(soccer_probe.requests.Session, "get", refuse)
    assert soccer_probe.probe_espn(soccer_probe.OLD_DAY)["status"].startswith("FAILED")
    assert soccer_probe.probe_understat(2026)["league_page"].startswith("FAILED")
    assert soccer_probe.probe_mls(2026)["matches"].startswith("FAILED")
