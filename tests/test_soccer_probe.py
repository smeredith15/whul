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


def test_commentary_is_read_for_shots_and_who_set_them_up():
    payload = {"commentary": [
        {"time": {"displayValue": "12'"},
         "text": "Goal! Arsenal 1, Chelsea 0. Bukayo Saka (Arsenal) left footed shot "
                 "from outside the box to the top left corner. Assisted by Martin Odegaard."},
        {"time": {"displayValue": "30'"},
         "text": "Attempt saved. Cole Palmer (Chelsea) right footed shot from the "
                 "centre of the box is saved in the centre of the goal."},
        {"time": {"displayValue": "45'+2'"},
         "text": "Attempt missed. Declan Rice (Arsenal) header from the centre of the "
                 "box misses to the left. Assisted by Bukayo Saka with a cross."},
        {"time": {"displayValue": "50'"}, "text": "Corner,  Chelsea. Conceded by William Saliba."},
    ]}
    report = soccer_probe.commentary_report(payload)
    assert report["goals_and_attempts"] == 3
    assert report["of_which_assisted"] == 2
    assert report["corners"] == 1
    assert [m for m, _ in soccer_probe._lines(payload)] == [12, 30, 45, 50]


def test_a_page_s_embedded_data_is_searched_for_the_shot_map():
    html = ('<html><script id="__NEXT_DATA__" type="application/json">'
            '{"props": {"pageProps": {"content": {"shotmap": {"shots": '
            '[{"eventType": "Goal", "expectedGoals": 0.07}]}}}}}</script></html>')
    shots = soccer_probe._find_key(soccer_probe._next_data(html), "shots")
    assert shots[0]["expectedGoals"] == 0.07


def test_the_shot_xg_hosts_report_a_refusal_rather_than_raise(monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr(soccer_probe.requests.Session, "get", refuse)
    assert soccer_probe.probe_sofascore()["status"].startswith("FAILED")
    assert soccer_probe.probe_fotmob()["status"].startswith("no Champions League")
