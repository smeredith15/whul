"""Shared helpers for the per-league scoring modules.

Upstream feeds rename columns between releases -- nflverse has shipped both
``passing_interceptions`` and ``interceptions``, and both ``team`` and
``recent_team``. The R scripts handled this with ``get_num_col`` / ``get_char_col``
candidate lists; these are the Python equivalents, so a rename upstream degrades
into a clear error rather than a silently zeroed stat.
"""

from __future__ import annotations

import pandas as pd


def resolve_num(
    df: pd.DataFrame,
    candidates: list[str],
    default: float = 0.0,
    required: bool = False,
) -> pd.Series:
    """First present candidate column, coerced to numeric."""
    for col in candidates:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").fillna(default)
    if required:
        raise KeyError(f"None of {candidates} present; have {sorted(df.columns)[:25]}")
    return pd.Series(default, index=df.index, dtype="float64")


def resolve_str(
    df: pd.DataFrame,
    candidates: list[str],
    default: str = "",
    required: bool = False,
) -> pd.Series:
    """First present candidate column, coerced to string."""
    for col in candidates:
        if col in df.columns:
            return df[col].astype("string").fillna(default)
    if required:
        raise KeyError(f"None of {candidates} present; have {sorted(df.columns)[:25]}")
    return pd.Series(default, index=df.index, dtype="string")
