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


def test_buffer_pool_ranks_across_the_whole_draft_pool():
    """La Liga and Serie A share the Club Soccer Top 3 pool, so they compete."""
    df = pd.DataFrame(
        {
            "league": ["La Liga"] * 100 + ["Serie A"] * 100,
            "role": ["FW"] * 200,
            "total_points": list(range(200, 100, -1)) + list(range(100, 0, -1)),
        }
    )
    pool = buffer_pool(df, "Player")
    assert len(pool) == round(90 * 1.5) == 135
    assert set(pool["draft_pool"]) == {"Club Soccer Top 3"}


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
