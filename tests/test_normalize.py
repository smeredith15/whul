import pandas as pd
import pytest

from whul.normalize import apply_benchmarks, assign_norm_key, buffer_pool, compute_benchmarks, scale


def test_quantile_matches_r_type_7():
    """R's quantile(x, 0.99) default (type 7) is linear interpolation.

    For x = 1..100: h = (n-1)p + 1 = 99.01, so the result is 99 + 0.01*(100-99).
    """
    s = pd.Series(range(1, 101))
    assert s.quantile(0.99) == pytest.approx(99.01)


def test_scale_puts_benchmark_at_100():
    total = pd.Series([200.0, 100.0, 50.0])
    bench = pd.Series([200.0, 200.0, 200.0])
    assert list(scale(total, bench)) == [100.0, 50.0, 25.0]


def test_scores_may_exceed_100():
    """100 is the 99th percentile, not a ceiling."""
    assert scale(pd.Series([260.0]), pd.Series([200.0])).iloc[0] == 130.0


def test_norm_key_splits_basketball_by_court():
    df = pd.DataFrame(
        {"league": ["NBA", "NBA", "WNBA"], "role": ["PG", "C", "SG"]}
    )
    assert list(assign_norm_key(df, "Player")) == ["NBA_Backcourt", "NBA_Frontcourt", "WNBA_Backcourt"]


def test_norm_key_splits_nfl_and_mlb_by_role():
    df = pd.DataFrame({"league": ["NFL", "MLB"], "role": ["QB", "Pitcher"]})
    assert list(assign_norm_key(df, "Player")) == ["NFL_QB", "MLB_Pitcher"]


def test_norm_key_falls_back_to_league():
    df = pd.DataFrame({"league": ["PGA", "Tennis"], "role": ["Golfer", "Athlete"]})
    assert list(assign_norm_key(df, "Player")) == ["PGA", "Tennis"]


def test_teams_normalize_by_league():
    df = pd.DataFrame({"league": ["NFL", "MLB"]})
    assert list(assign_norm_key(df, "Team")) == ["NFL", "MLB"]


def test_buffer_pool_truncates_to_buffer_n():
    """NFL players: Target_N 45 * 1.50 = 68, so only the top 68 survive."""
    df = pd.DataFrame(
        {
            "league": ["NFL"] * 100,
            "role": ["QB"] * 100,
            "total_points": range(100, 0, -1),
        }
    )
    pool = buffer_pool(df, "Player")
    assert len(pool) == 68
    assert pool["total_points"].min() == 33  # 100 - 68 + 1


def test_each_position_keeps_its_own_pool():
    """Truncation is per normalization group, so a thin position is never squeezed
    out by higher-scoring positions sharing its draft pool.

    Every TE here scores below every QB. Ranking across the NFL draft pool would
    discard the tight ends entirely; ranking within each position keeps both.
    """
    df = pd.DataFrame(
        {
            "league": ["NFL"] * 200,
            "role": ["QB"] * 100 + ["TE"] * 100,
            "total_points": list(range(300, 200, -1)) + list(range(100, 0, -1)),
        }
    )
    pool = buffer_pool(df, "Player")
    by_group = pool.groupby("norm_key").size().to_dict()
    assert by_group == {"NFL_QB": 68, "NFL_TE": 68}, "each position truncates separately"


def test_soccer_leagues_normalize_separately():
    """Norm groups for club soccer are individual leagues, so each gets its own
    pool -- La Liga players are measured against La Liga, not against Serie A."""
    df = pd.DataFrame(
        {
            "league": ["La Liga"] * 100 + ["Serie A"] * 100,
            "role": ["FW"] * 200,
            "total_points": list(range(200, 100, -1)) + list(range(100, 0, -1)),
        }
    )
    pool = buffer_pool(df, "Player")
    assert set(pool["draft_pool"]) == {"Club Soccer Top 3"}
    assert set(pool["norm_key"]) == {"La Liga", "Serie A"}
    # buffer_n is 135 per group and each league has only 100, so all survive
    assert len(pool) == 200


def test_benchmark_is_computed_after_truncation():
    """The benchmark describes the draftable pool, not the whole league.

    Truncating to the buffer pool discards the weak tail, so the pool's 99th
    percentile sits *above* the whole field's -- 100 is a genuinely elite mark
    among draftable assets rather than among every professional.
    """
    df = pd.DataFrame(
        {"league": ["NFL"] * 500, "role": ["QB"] * 500, "total_points": range(500, 0, -1)}
    )
    truncated = compute_benchmarks(df, "Player").iloc[0]["benchmark"]
    whole_field = df["total_points"].quantile(0.99)
    assert truncated > whole_field
    assert truncated == pytest.approx(pd.Series(range(500, 500 - 68, -1)).quantile(0.99))


def test_unmapped_league_raises_rather_than_silently_dropping():
    """A typo'd or newly added league must not quietly vanish from scoring."""
    df = pd.DataFrame({"league": ["Kabaddi"], "role": ["Raider"], "total_points": [10.0]})
    with pytest.raises(ValueError, match="Kabaddi"):
        buffer_pool(df, "Player")


def test_apply_benchmarks_roundtrip():
    df = pd.DataFrame(
        {"league": ["NFL"] * 80, "role": ["QB"] * 80, "total_points": range(80, 0, -1)}
    )
    bench = compute_benchmarks(df, "Player")
    scored = apply_benchmarks(df, bench, "Player")
    assert scored["scaled_score"].notna().all()
    top = scored.loc[scored["total_points"].idxmax()]
    assert top["scaled_score"] == pytest.approx(
        top["total_points"] / bench.iloc[0]["benchmark"] * 100, abs=0.01
    )


def test_thin_positions_survive_truncation():
    """The case that previously produced silent NaN scores.

    Every TE scores below every QB. Under draft-pool ranking a small pool kept
    only QBs and left every TE unscoreable; per-group ranking scores both.
    """
    df = pd.DataFrame({
        "league": ["NFL"] * 30,
        "role": ["QB"] * 25 + ["TE"] * 5,
        "total_points": list(range(100, 75, -1)) + [5, 4, 3, 2, 1],
    })
    bench = compute_benchmarks(df, "Player", managers=1)
    assert {"NFL_QB", "NFL_TE"} == set(bench["norm_key"])
    scored = apply_benchmarks(df, bench, "Player")
    assert scored["scaled_score"].notna().all()
    # The best TE is elite among TEs even though he trails every QB outright.
    best_te = scored[scored["role"] == "TE"].nlargest(1, "total_points").iloc[0]
    assert best_te["scaled_score"] > 90


def test_missing_benchmark_still_raises():
    """A frozen benchmark set that lacks a group must not score NaN silently --
    e.g. a position appearing mid-season that the frozen set never saw."""
    df = pd.DataFrame({
        "league": ["NFL"] * 6,
        "role": ["QB"] * 3 + ["TE"] * 3,
        "total_points": [100.0, 90.0, 80.0, 50.0, 40.0, 30.0],
    })
    bench = compute_benchmarks(df, "Player")
    frozen = bench[bench["norm_key"] != "NFL_TE"]
    with pytest.raises(ValueError, match="NFL_TE"):
        apply_benchmarks(df, frozen, "Player")


def test_non_strict_mode_allows_nan_for_exploration():
    df = pd.DataFrame({
        "league": ["NFL"] * 6,
        "role": ["QB"] * 3 + ["TE"] * 3,
        "total_points": [100.0, 90.0, 80.0, 50.0, 40.0, 30.0],
    })
    frozen = compute_benchmarks(df, "Player")
    frozen = frozen[frozen["norm_key"] != "NFL_TE"]
    out = apply_benchmarks(df, frozen, "Player", strict=False)
    assert out["scaled_score"].isna().sum() == 3


# --- one league, one distribution ------------------------------------------

def test_each_tour_and_series_is_measured_against_itself():
    """ATP against ATP and WTA against WTA, F1 against F1 and NASCAR against
    NASCAR. They share a roster category, not a distribution."""
    df = pd.DataFrame({
        "league": ["ATP", "WTA", "NASCAR", "F1"],
        "role": ["Singles", "Singles", "Driver", "Driver"],
    })
    assert list(assign_norm_key(df, "Player")) == ["ATP", "WTA", "NASCAR", "F1"]


def test_a_stray_norm_league_column_no_longer_pools_anything():
    """Nothing writes this column any more. A frame carrying one from an older
    cache must still normalize by league, or two leagues would silently merge."""
    df = pd.DataFrame({
        "league": ["ATP", "WTA"],
        "norm_league": ["Tennis", "Tennis"],
        "role": ["Singles", "Singles"],
    })
    assert list(assign_norm_key(df, "Player")) == ["ATP", "WTA"]


def test_the_league_decides_for_a_sport_with_no_position_split():
    df = pd.DataFrame({"league": ["NHL", "PGA"], "role": ["Skater", "Golfer"]})
    assert list(assign_norm_key(df, "Player")) == ["NHL", "PGA"]


def test_each_club_soccer_league_stands_on_its_own():
    df = pd.DataFrame({
        "league": ["Premier League", "La Liga", "Serie A", "Bundesliga",
                   "Ligue 1", "MLS", "NWSL"],
    })
    assert len(set(assign_norm_key(df, "Team"))) == 7


def test_positions_still_split_a_league_that_has_them():
    df = pd.DataFrame({"league": ["NFL", "NFL"], "role": ["QB", "TE"]})
    assert list(assign_norm_key(df, "Player")) == ["NFL_QB", "NFL_TE"]
