"""Best of the rest: the strongest roster nobody drafted, once a season is over."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

from whul import best_rest
from whul.store import open_store

SEASON = "2026-27"
DAY = "2026-10-29"


def _store():
    store = open_store(":memory:")
    store.upsert("benchmark_versions", [{
        "version": "v", "season": SEASON, "quantile": 0.99, "managers": 15,
        "computed_at": "2026-10-01", "frozen_at": "2026-10-01"}], keys=("version",))
    store.upsert("benchmarks", [
        {"version": "v", "asset_type": t, "norm_key": k, "benchmark": b, "pool_size": 10}
        for t, k, b in (("Player", "MLB_Batter", 1000.0), ("Player", "MLB_Pitcher", 1000.0),
                        ("Team", "MLB", 300.0))], keys=("version", "asset_type", "norm_key"))
    for manager, name in (("SM", "Scott"), ("JM", "Jake")):
        store.upsert("managers", [{"manager_id": manager, "display_name": name}],
                     keys=("manager_id",))
    # Scott holds Ohtani and the Dodgers; Jake holds Judge. Two season slots
    # each, and Scott's best-games slot, which the comparison leaves out.
    held = [("SM", "Player", "Shohei Ohtani", 1, 80.0, "season"),
            ("SM", "Player", "Paul Skenes", 2, 70.0, "season"),
            ("SM", "Player", "Pete Alonso", 3, 99.0, "best"),
            ("SM", "Team", "Los Angeles Dodgers", 1, 90.0, "season"),
            ("JM", "Player", "Aaron Judge", 1, 60.0, "season")]
    for manager, kind, name, index, score, mode in held:
        asset = f"{kind.lower()}-mlb-{name.lower().replace(' ', '-')}"
        store.upsert("assets", [{"asset_id": asset, "asset_type": kind, "display_name": name,
                                 "league": "MLB", "created_at": "2026-08-21"}], keys=("asset_id",))
        slot = f"{manager}:{kind}:{index}"
        store.upsert("roster_slots", [{"slot_id": slot, "manager_id": manager, "season": SEASON,
                                       "category": "MLB", "asset_type": kind,
                                       "slot_index": index}], keys=("slot_id",))
        store.upsert("slot_occupancy", [{"slot_id": slot, "asset_id": asset,
                                         "start_date": "2026-08-21", "end_date": None}],
                     keys=("slot_id", "start_date"))
        store.upsert("slot_scores", [{"slot_id": slot, "season": SEASON, "as_of": DAY,
                                      "asset_id": asset, "score": score, "counts": 1,
                                      "best_score": score, "scored_as": mode}],
                     keys=("slot_id", "as_of"))
    return store


def _pull(source, as_of):
    if source.asset_type == "Team":
        return pd.DataFrame({"team": ["Los Angeles Dodgers", "Toronto Blue Jays",
                                      "Seattle Mariners", "Detroit Tigers"],
                             "league": "MLB", "season": 2026,
                             "total_points": [300.0, 240.0, 210.0, 150.0]})
    return pd.DataFrame({
        "player": ["Shohei Ohtani", "Aaron Judge", "Cal Raleigh", "Bobby Witt Jr.",
                   "Garrett Crochet", "Zack Wheeler", "Jose Altuve"],
        "team": ["LAD", "NYY", "SEA", "KC", "BOS", "PHI", "HOU"],
        "league": "MLB",
        "role": ["Batter", "Batter", "Batter", "Batter", "Pitcher", "Pitcher", "Batter"],
        "season": 2026, "total_points": [950, 900, 850, 800, 780, 600, 500]})


@pytest.fixture
def result():
    return best_rest.build(_store(), best_rest.GROUPS["mlb-2026"], date(2026, 11, 22),
                           pull=_pull)


def test_nobody_drafted_is_on_it(result):
    names = {p.name for c in result.categories for p in c.picks}
    assert not names & {"Shohei Ohtani", "Aaron Judge", "Los Angeles Dodgers"}


def test_the_slots_fill_best_first_as_a_roster_does(result):
    players = next(c for c in result.categories if c.asset_type == "Player")
    # MLB players: two season slots, one best-games slot, one bench.
    assert [p.slot for p in players.picks] == ["season", "season", "best", "bench"]
    scores = [p.score for p in players.picks]
    assert scores == sorted(scores, reverse=True)
    assert players.picks[0].name == "Cal Raleigh"
    teams = next(c for c in result.categories if c.asset_type == "Team")
    assert [p.name for p in teams.picks] == ["Toronto Blue Jays", "Seattle Mariners"]


def test_managers_are_compared_on_their_season_slots_alone(result):
    players = next(c for c in result.categories if c.asset_type == "Player")
    # Scott's best-games slot (Alonso, 99) is not in his figure.
    assert players.managers == {"SM": 150.0, "JM": 60.0}
    assert result.compared_on == date(2026, 10, 29)
    rest, managers = result.totals()
    assert managers == {"SM": 240.0, "JM": 60.0}
    assert rest == pytest.approx(85 + 80 + 80 + 70)
    assert "#1 of 3" in result.headline()


def test_the_lists_hold_only_the_slots_that_are_compared(result):
    """A best-games pick in the list read as though it counted. It does not."""
    page = best_rest.render(result)
    assert "Best of the rest: MLB 2026" in page
    lists = page.split("<div class=\"row\">")[1]
    # Raleigh and Witt fill the two season slots and are listed; Crochet
    # fills the best-games slot and Wheeler the bench, and neither is.
    assert "Cal Raleigh" in lists and "Bobby Witt Jr." in lists
    assert "Garrett Crochet" not in lists and "Zack Wheeler" not in lists
    assert "not compared" not in page
    # And nothing is written under the page.
    assert "<footer" not in page


def test_each_panel_charts_drafted_and_undrafted_together(result):
    players = next(c for c in result.categories if c.asset_type == "Player")
    assert {h.name for h in players.held} == {"Shohei Ohtani", "Paul Skenes",
                                              "Pete Alonso", "Aaron Judge"}
    chart = best_rest._chart(players, {"SM": "#1baf7a", "JM": "#2a78d6"})
    # Drafted bars in their manager's colour, undrafted ones hatched.
    assert "Shohei Ohtani" in chart and "Cal Raleigh" in chart
    assert chart.count("url(#hatch)") == 2
    # Best first, whoever holds them.
    assert chart.index("Cal Raleigh") < chart.index("Shohei Ohtani") < chart.index("Aaron Judge")


def test_mlb_year_one_is_the_first_season_alone():
    """Run in 2027 it must still be 2026: the second half is its own group."""
    group = best_rest.GROUPS["mlb-2026"]
    source = best_rest._source("mlb", group)
    assert source.seasons_for(date(2027, 5, 1)) == [2026]
    # The college sports read the whole division, not the drafted names.
    assert not best_rest._source("ncaam", best_rest.GROUPS["ncaam"]).roster_scoped
    # MLB's players keep their own loader, which is league-wide without names.
    assert best_rest._source("mlb", group).live is not None


def test_each_group_is_ready_when_the_league_said():
    from whul.config.league import SEASON

    for key in ("mlb", "tennis", "pga", "motorsports"):
        assert best_rest.GROUPS[key].ready() == SEASON.end
    assert best_rest.GROUPS["intl-soccer"].ready() > date(2027, 7, 25)
    assert best_rest.GROUPS["mlb-2026"].ready() < date(2027, 1, 1)


def test_the_workflow_offers_every_group():
    spec = yaml.safe_load((Path(__file__).resolve().parents[1]
                           / ".github/workflows/best-of-rest.yml").read_text())
    on = spec.get("on", spec.get(True))
    options = on["workflow_dispatch"]["inputs"]["group"]["options"]
    assert sorted(options) == sorted(best_rest.GROUPS)
