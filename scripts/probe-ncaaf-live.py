"""Where the NCAAF live pull loses its rows.

``ingest ncaaf`` reports "the source has no results yet for this season",
which is what it says whenever the frame arrives empty -- and there are five
separate places between ESPN and the scorer that can empty it without a word:

  1. the season number asked for (2027 is a season nobody has played)
  2. a rostered name the team index does not know
  3. a schedule with no completed games
  4. the league-start filter, if every game falls before it
  5. score_football's conference filter, which drops every game whose
     conference is blank and returns an empty frame in silence

This walks the same chain ``ingest`` walks and prints the count after each
step, so one run says which of the five it is.
"""
import json

from datetime import date

import pandas as pd

from whul import ingest as ingest_module
from whul import resolve as resolver
from whul.benchmark_sources import SOURCES
from whul.config.league import season_start
from whul.scoring.ncaa import SCORERS, _team_games
from whul.sources import espn
from whul.store import open_store

LEAGUE = "ncaaf"
CATEGORY = "NCAAF"
SEASON = "2026-27"
DB = "data/whul.sqlite3"

pd.set_option("display.width", 200)


def head(n, text):
    print()
    print("=" * 68)
    print(f"{n}. {text}")
    print("=" * 68)


head(0, "what season are we asking ESPN for?")
today = date.today()
label = espn.season_label(LEAGUE, today)
print(f"  today {today} -> season {label}")
print(f"  SEASON_WINDOWS[{LEAGUE!r}] = {espn.SEASON_WINDOWS[LEAGUE]}")
if label != today.year:
    print("  ^ WRONG. College football is numbered by the year it starts.")
    print("    You are running code from before the numbering fix -- git pull.")
    raise SystemExit(1)
print(f"  league start date: {season_start(CATEGORY)}")

head(1, "what is rostered?")
store = open_store(DB)
assets = resolver.rostered_assets(store, SEASON, "Team")
mine = assets[assets["league"].isin(ingest_module._leagues_of(SOURCES[LEAGUE]))]
names = list(mine["display_name"])
print(f"  {len(names)} rostered: {names}")
if not names:
    raise SystemExit("nothing rostered -- stop here")

head(2, "does ESPN's team index know those names?")
index = espn.team_index(LEAGUE)
print(f"  index holds {len(index)} teams")
lookup = {espn._match_key(k): (k, v) for k, v in index.items()}
ids = {}
for name in names:
    hit = lookup.get(espn._match_key(name))
    print(f"  {'ok  ' if hit else 'MISS'} {name}" + (f"  -> id {hit[1]}" if hit else ""))
    if hit:
        ids[name] = hit[1]

head(3, "raw schedule rows, per team")
frames = []
for name, team_id in ids.items():
    try:
        df = espn.load_team_schedule(LEAGUE, team_id, label)
    except Exception as exc:
        print(f"  {name}: ERR {type(exc).__name__}: {exc}")
        continue
    if df.empty:
        print(f"  {name}: 0 rows")
        continue
    done = int(df["completed"].sum())
    scored = int(df["home_score"].notna().sum())
    conf = int((df["home_conference"].astype(str) != "").sum())
    print(f"  {name}: {len(df)} rows, {done} completed, {scored} with a score, "
          f"{conf} with a home conference")
    frames.append(df)

if not frames:
    raise SystemExit("no schedule rows at all -- stop here")
raw = pd.concat(frames, ignore_index=True)
print(f"\n  total {len(raw)} rows")
print(raw[["game_date", "completed", "home_team", "home_score", "away_score",
           "away_team", "home_conference", "away_conference"]].to_string())

head(4, "after the league-start filter")
kept = ingest_module._from_season_start(raw, CATEGORY)
print(f"  {len(raw)} -> {len(kept)} rows (start {season_start(CATEGORY)})")
if kept.empty:
    raise SystemExit("every game falls before the league start date")

head(5, "after _team_games (completed, with scores)")
games = _team_games(kept)
print(f"  {len(kept)} game rows -> {len(games)} team-game rows")
if games.empty:
    raise SystemExit("nothing completed with a score")
blank = (games["conference"].isna() | (games["conference"].astype(str) == "")).sum()
print(f"  of those, {blank} have a blank conference "
      f"(score_football drops every one of them)")

head(6, "after score_football")
scored = SCORERS[CATEGORY](kept, set(kept["home_team"]) | set(kept["away_team"]))
print(f"  {len(scored)} scored teams")
if scored.empty:
    print("  ^ THIS is where the rows go. Almost certainly the conference filter.")
else:
    print(scored.to_string())

head(7, "one raw competitor, so we can see where ESPN hides the conference")
sport, path = espn.LEAGUE_PATHS[LEAGUE]
team_id = next(iter(ids.values()))
payload = espn._get(f"{espn.BASE}/{sport}/{path}/teams/{team_id}/schedule",
                    {"season": label})
event = (payload.get("events") or [{}])[0]
competitor = ((event.get("competitions") or [{}])[0].get("competitors") or [{}])[0]
print("  competitor keys:", sorted(competitor))
print("  team keys:      ", sorted((competitor.get("team") or {})))
for key in ("conferenceId", "groups", "group"):
    if key in competitor:
        print(f"  competitor[{key!r}] = {json.dumps(competitor[key])[:300]}")
    team = competitor.get("team") or {}
    if key in team:
        print(f"  team[{key!r}] = {json.dumps(team[key])[:300]}")
print("  event keys:     ", sorted(event))
