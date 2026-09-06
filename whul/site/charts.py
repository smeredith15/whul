"""Inline SVG charts.

Hand-drawn SVG rather than a charting library: five managers and one update a
day does not justify a dependency, and the output stays readable, printable and
free of anything that has to load before the page means something.

Two forms, each doing one job:

* **Progression** -- a multi-series line. The job is change over time, and the
  question it answers is who has been gaining, not merely who leads now.
* **Contribution** -- grouped horizontal bars, one row per roster category. The
  job is comparing magnitude across a nominal axis. Grouped rather than stacked
  because the question is "who is getting points from where", which a stack
  makes you measure segment lengths to answer.

Colour is by manager on both, so identity carries between them. Categories are
distinguished by position and label, never by hue -- there are 21 of them and
eight is the ceiling for colour that means something.

Every chart ships a table view. It is the relief the light-mode contrast
warning requires, and it is what makes a value readable without a hover.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from html import escape

# Mark specs, fixed across every chart here.
LINE_WIDTH = 2
END_MARKER_RADIUS = 4.5      # >= 4, so the marker is >= 8px across
SURFACE_RING = 2
BAR_MAX_THICKNESS = 14       # <= 24; the row's leftover is air
BAR_GAP = 2                  # surface gap between touching marks
BAR_RADIUS = 4               # rounded data-end, square at the baseline
GRID_STEPS = 5


@dataclass(frozen=True)
class Series:
    name: str
    slot: int                # 1-based categorical slot; fixed per manager
    values: list[float]

    @property
    def color(self) -> str:
        return f"var(--series-{self.slot})"


def _nice_ceiling(value: float) -> float:
    """A round number at or above the data's top, for the axis."""
    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if magnitude * step >= value:
            return magnitude * step
    return magnitude * 10


def _fmt(value: float) -> str:
    """Axis and label numbers. Zero is written as zero, not 0.0."""
    if value == 0:
        return "0"
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.1f}"


def progression_chart(
    days: list[date],
    series: list[Series],
    width: int = 1000,
    height: int = 360,
    chart_id: str = "progression",
) -> str:
    """A multi-series line chart with a hover crosshair and direct end labels.

    End labels are what make the lines identifiable without relying on colour,
    which the light-mode palette requires. They are placed only if they fit;
    where they would collide they are dropped in favour of the legend, since a
    stack of overlapping labels is worse than none.
    """
    if not days or not series:
        return '<p class="sub">No scores recorded yet.</p>'

    pad_left, pad_right, pad_top, pad_bottom = 56, 108, 16, 34
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    top = _nice_ceiling(max((max(s.values) for s in series), default=1))
    steps = max(len(days) - 1, 1)

    def x(i: int) -> float:
        return pad_left + plot_w * i / steps

    def y(value: float) -> float:
        return pad_top + plot_h * (1 - value / top)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Total score by day for {len(series)} managers" '
        f'class="linechart" data-chart="{chart_id}">'
    ]

    # Gridlines: hairline, solid, recessive.
    for step in range(GRID_STEPS + 1):
        value = top * step / GRID_STEPS
        gy = y(value)
        parts.append(
            f'<line x1="{pad_left}" y1="{gy:.1f}" x2="{pad_left + plot_w}" y2="{gy:.1f}" '
            f'stroke="var(--grid)" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_left - 8}" y="{gy + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="var(--muted)">{_fmt(value)}</text>'
        )

    # Date axis: first, middle and last only -- one label per day is unreadable.
    # Indexed off the days themselves rather than off ``steps``, which is
    # floored at 1 so the x-scale never divides by zero: on the season's first
    # day there is one day and no ``days[1]`` to label it with.
    last = len(days) - 1
    for index in sorted({0, last // 2, last}):
        parts.append(
            f'<text x="{x(index):.1f}" y="{height - 12}" text-anchor="middle" '
            f'font-size="11" fill="var(--muted)">{days[index].strftime("%d %b %Y")}</text>'
        )

    for entry in series:
        points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(entry.values))
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{entry.color}" '
            f'data-manager="{escape(entry.name)}" '
            f'stroke-width="{LINE_WIDTH}" stroke-linejoin="round" '
            f'stroke-linecap="round"/>'
        )

    # End markers and direct labels. Labels are placed top-down in value order
    # and each is pushed just far enough below the previous one to clear it, so
    # the sequence stays monotonic and matches the order of the lines. The
    # earlier version nudged each label away from its nearest neighbour, which
    # could move one past another and leave a label pointing at the wrong line.
    finals = sorted(series, key=lambda s: s.values[-1], reverse=True)
    min_gap = 16
    previous: float | None = None
    for entry in finals:
        ex, ey = x(steps), y(entry.values[-1])
        parts.append(
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="{END_MARKER_RADIUS}" '
            f'data-manager="{escape(entry.name)}" '
            f'fill="{entry.color}" stroke="var(--surface-1)" '
            f'stroke-width="{SURFACE_RING}"/>'
        )
        label_y = ey if previous is None else max(ey, previous + min_gap)
        previous = label_y
        # A leader line where the label had to move, so it still reads as
        # belonging to its own series rather than to whatever it now sits by.
        if label_y - ey > 2:
            parts.append(
                f'<path d="M{ex + 5:.1f},{ey:.1f} L{ex + 8:.1f},{label_y:.1f}" '
                f'data-manager="{escape(entry.name)}" '
                f'stroke="{entry.color}" stroke-width="1" fill="none" opacity="0.6"/>'
            )
        parts.append(
            f'<text x="{ex + 11:.1f}" y="{label_y + 4:.1f}" font-size="12" '
            f'data-manager="{escape(entry.name)}" '
            f'fill="var(--text-secondary)">{escape(entry.name)} '
            f'<tspan fill="var(--text-primary)" font-weight="600">'
            f'{_fmt(entry.values[-1])}</tspan></text>'
        )

    parts.append(
        f'<line class="crosshair" x1="0" y1="{pad_top}" x2="0" y2="{pad_top + plot_h}" '
        f'stroke="var(--axis)" stroke-width="1" opacity="0"/>'
    )
    parts.append(
        f'<rect class="hitbox" x="{pad_left}" y="{pad_top}" width="{plot_w}" '
        f'height="{plot_h}" fill="transparent"/>'
    )
    parts.append("</svg>")

    payload = {
        "id": chart_id,
        "padLeft": pad_left, "plotW": plot_w,
        "days": [d.isoformat() for d in days],
        "series": [
            {"name": s.name, "slot": s.slot, "values": [round(v, 2) for v in s.values]}
            for s in series
        ],
    }
    return (
        '<div class="chart">' + "".join(parts) + "</div>"
        f'<script type="application/json" class="chartdata">{json.dumps(payload)}</script>'
    )


#: Each slot's own bar is thinner than a category's was: 47 rows of five needs
#: the height back.
SLOT_BAR_THICKNESS = 9
#: Room under a category for its bars, plus a line for the category's own name.
#: The name used to sit in the label column beside the first bar; now that every
#: bar is labelled with its asset, it needs its own line or the two collide.
SLOT_ROW_PAD = 24
SLOT_HEADER_DROP = 12

#: The label column. Wide enough for a name and its type, which is what a bar
#: is actually of -- "#2" alone says a manager holds something in a category
#: without saying what.
SLOT_LABEL_WIDTH = 250

#: Teams are drawn solid and players a little softer, so a category holding
#: both reads as two kinds of thing rather than one run of bars. Alpha alone is
#: a weak signal, which is why the label carries the word as well.
PLAYER_ALPHA = 0.68


def _fit(text: str, limit: int) -> str:
    """A name short enough for the label column, cut on a character count.

    Crude next to measuring the glyphs, and enough: the column is sized for the
    longest name a roster actually holds, and the few that overrun are cut at a
    word rather than mid-syllable where that is possible.
    """
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    spaced = cut.rsplit(" ", 1)[0]
    return (spaced if len(spaced) >= limit - 8 else cut) + "\u2026"


def contribution_chart(
    rows: list[tuple[str, str, str]],
    managers: list[tuple[str, int]],
    values: dict[tuple[str, str], tuple[float, str, str]],
    width: int = 1000,
    chart_id: str = "contribution",
    depth: dict[str, int] | None = None,
    top: float | None = None,
) -> str:
    """One row per category, one run of bars per manager within it.

    Every bar is a single slot's normalized score, so every bar on the chart is
    on the same 0-100 scale and any two are directly comparable -- a category
    with four slots no longer dwarfs one with a single slot simply by having
    more of them.

    Bars are ordered ``(manager, rank)``: a manager's two NFL teams sit side by
    side in that manager's colour, then the next manager's two. Ordered the
    other way -- every manager's first slot, then every manager's second -- a
    category cannot be read as a block, because each manager's holding is split
    across the width of the chart.

    ``rows`` is ``(category, key, label)``; with ``depth`` given, one row per
    category carrying all of its ranks. ``values`` maps ``(manager, key)`` to
    ``(score, asset_id, asset name)``, the last two so a bar can open the
    profile it stands for.
    """
    if not rows or not managers:
        return '<p class="sub">Nothing scored yet.</p>'

    # One row per category now, not one per slot: the ranks live inside it.
    # Where no depth is given it is read off the rows themselves, which already
    # carry one entry per rank -- inferring 1 would silently draw a manager's
    # best slot and drop the rest.
    categories: list[str] = []
    counted: dict[str, int] = {}
    for category, _, _ in rows:
        if category not in categories:
            categories.append(category)
        counted[category] = counted.get(category, 0) + 1
    depth = depth or counted
    per_row = {c: max(depth.get(c, counted.get(c, 1)), 1) for c in categories}

    pad_left, pad_right, pad_top = SLOT_LABEL_WIDTH, 56, 8
    def _row_height(category: str) -> float:
        bars = len(managers) * per_row[category]
        return bars * (SLOT_BAR_THICKNESS + BAR_GAP) + SLOT_ROW_PAD

    heights = {c: _row_height(c) for c in categories}
    plot_h = sum(heights.values())
    height = pad_top + plot_h + 28
    plot_w = width - pad_left - pad_right
    # Shared across sections when one is passed in: a per-section ceiling would
    # make two sections' bars look alike at different scores.
    top = top or _nice_ceiling(max((v[0] for v in values.values()), default=1))

    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Normalized score for every roster slot, by manager" '
        f'class="barchart" data-chart="{chart_id}">'
    ]

    for step in range(GRID_STEPS + 1):
        value = top * step / GRID_STEPS
        gx = pad_left + plot_w * step / GRID_STEPS
        parts.append(
            f'<line x1="{gx:.1f}" y1="{pad_top}" x2="{gx:.1f}" '
            f'y2="{pad_top + plot_h:.1f}" '
            f'stroke="var(--grid)" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{gx:.1f}" y="{height - 10}" text-anchor="middle" '
            f'font-size="11" fill="var(--muted)">{_fmt(value)}</text>'
        )

    offset = pad_top
    for category in categories:
        base_y = offset + 4 + SLOT_HEADER_DROP
        # Above its bars rather than beside the first one, and anchored at the
        # left edge: the column to the right of it now belongs to the assets.
        parts.append(
            f'<text x="6" y="{offset + 12:.1f}" '
            f'font-size="12" fill="var(--text-primary)" font-weight="600">'
            f'{escape(category)}</text>'
        )
        parts.append(
            f'<line x1="6" y1="{base_y - 5:.1f}" '
            f'x2="{width - pad_right}" y2="{base_y - 5:.1f}" '
            f'stroke="var(--grid)" stroke-width="1"/>'
        )
        index = 0
        for manager, slot in managers:
            for rank in range(1, per_row[category] + 1):
                key = f"{category} {rank}"
                # Three entries or four: the fourth is the asset's kind, which
                # older callers do not carry.
                held = values.get((manager, key), (0.0, "", "", ""))
                score, asset_id, asset_name = held[0], held[1], held[2]
                kind = held[3] if len(held) > 3 else ""
                bar_y = base_y + index * (SLOT_BAR_THICKNESS + BAR_GAP) + 2
                index += 1
                # What the bar is of, not merely which slot it fills. Colour
                # says whose it is and the rank says where it sits; neither
                # says the manager holds Arsenal.
                # The rank is dropped where a category holds one slot: "#1"
                # down a column of single-slot categories is a column of ones.
                shown = _fit(asset_name, 22)
                if per_row[category] > 1:
                    label = f"#{rank} {shown}" if shown else f"#{rank}"
                else:
                    label = shown
                suffix = (f'<tspan fill="var(--muted)" font-size="9"> '
                          f'{escape(kind)}</tspan>') if kind else ""
                parts.append(
                    f'<text x="{pad_left - 10}" '
                    f'y="{bar_y + SLOT_BAR_THICKNESS - 1:.1f}" text-anchor="end" '
                    f'font-size="10.5" fill="var(--text-secondary)">'
                    f'{escape(label)}{suffix}</text>'
                )
                bar_w = max(plot_w * score / top, 0.0)
                radius = min(BAR_RADIUS, bar_w / 2) if bar_w else 0
                # Players a little softer than teams, so a mixed category reads
                # as two kinds of holding.
                alpha = f' fill-opacity="{PLAYER_ALPHA}"' if kind == "Player" else ""
                parts.append(
                    f'<rect class="bar" x="{pad_left}" y="{bar_y:.1f}" '
                    f'width="{bar_w:.1f}" height="{SLOT_BAR_THICKNESS}" '
                    f'rx="{radius:.1f}" fill="var(--series-{slot})"{alpha} '
                    # Name first. This is what a hover shows and what a screen
                    # reader reads, and "Club Soccer Top 3 1" answers neither
                    # "who is this" nor "how are they doing".
                    f'data-manager="{escape(manager)}" data-category="{escape(key)}" '
                    f'data-asset="{escape(asset_id)}" '
                    f'data-name="{escape(asset_name)}" data-kind="{escape(kind)}" '
                    f'data-value="{score:.1f}"><title>'
                    f'{escape(asset_name or "(empty)")}'
                    f'{" — " + escape(kind) if kind else ""} · '
                    f'{escape(category)} #{rank} · {escape(manager)} · '
                    f'{_fmt(score)}</title></rect>'
                )
        offset += heights[category]
    parts.append("</svg>")
    return '<div class="chart">' + "".join(parts) + "</div>"


#: Segments a part-to-whole figure may carry. Past this, angle comparison stops
#: working and adjacent hues blur -- so the rest folds into one "Other" slice
#: and the table underneath carries the detail. A manager has nine contributing
#: categories today and will have twice that once every league is in season,
#: which is a table's job rather than a pie's.
DONUT_SEGMENTS = 6
DONUT_OTHER = "Other"

#: "Other" is painted in the neutral rather than given a hue of its own.
#:
#: Two reasons, and they agree. It is a remainder rather than a category, so it
#: should recede next to the five things actually being compared. And the
#: palette will not carry six hues in a ring where any wedge may be matched
#: against any other: validated all-pairs, ``#008300`` against ``#eb6834`` is
#: 3.2 apart for a protanope -- indistinguishable -- and the pink against the
#: orange is 12.9 for everyone, under the floor of 15. Spending the sixth hue
#: on a bucket is what forced that. Five hues plus a neutral passes.
#:
#: The five that remain are checked adjacent, which is the scope that fits a
#: ring: wedges in a fixed rank order, each one touching its neighbours, and
#: the wrap from the last back to the first checked too. Both modes pass. The
#: contrast warning that comes with it obliges visible labels or a table, and
#: this figure carries both.
DONUT_OTHER_FILL = "var(--muted)"

#: How far an asset inside a segment may fade from its category's colour. Every
#: asset stays recognisably the category's hue; the alpha says only "these are
#: several holdings", never which is which -- the labels and the table do that.
DONUT_MIN_ALPHA = 0.45

#: Room either side of the ring for the direct category labels, and the longest
#: label that fits in it. A label sits ten pixels outside the ring and runs
#: outwards from there, so without a gutter the ends of the words are simply
#: outside the viewBox -- clipped rather than wrapped, and clipped silently.
DONUT_GUTTER = 104
DONUT_LABEL_CHARS = 18

#: The same, above and below. A label at twelve or six o'clock sits ten pixels
#: outside a ring that already reaches within six of the box.
DONUT_VPAD = 16


def _arc(cx: float, cy: float, r: float, start: float, end: float) -> str:
    """A pie wedge's outer arc, as path commands."""
    import math

    x1, y1 = cx + r * math.cos(start), cy + r * math.sin(start)
    x2, y2 = cx + r * math.cos(end), cy + r * math.sin(end)
    large = 1 if (end - start) > math.pi else 0
    return f"L {x1:.2f} {y1:.2f} A {r:.2f} {r:.2f} 0 {large} 1 {x2:.2f} {y2:.2f}"


def donut_chart(
    parts: list[tuple[str, list[tuple[str, float, str]]]],
    total: float,
    chart_id: str = "mix",
    size: int = 260,
) -> str:
    """Where a manager's counting total comes from.

    ``parts`` is ``(category, [(asset name, score, asset id)])``, biggest first.
    A category is one hue and the holdings inside it step down in alpha, so a
    segment reads as one category made of several things without the alpha
    having to be decoded -- which it cannot be, and is why the labels and the
    table carry the identities.

    A donut rather than a pie: the middle is where the total goes, and a number
    a reader wants is better in the figure than beside it.
    """
    import math

    if not parts or total <= 0:
        return '<p class="sub">Nothing counting yet.</p>'

    cx = cy = size / 2
    outer, inner = size / 2 - 6, size / 2 - 40
    angle = -math.pi / 2          # twelve o'clock
    gap = 0.012                   # the 2px surface gap, in radians at this radius
    wedges, labels, rows = [], [], []

    for index, (category, holdings) in enumerate(parts):
        fill = (DONUT_OTHER_FILL if category == DONUT_OTHER
                else f"var(--series-{index + 1})")
        share = sum(score for _, score, _ in holdings)
        if share <= 0:
            continue
        span = 2 * math.pi * share / total
        steps = max(len(holdings), 1)
        within = angle
        for depth, (name, score, asset_id) in enumerate(holdings):
            if score <= 0:
                continue
            piece = 2 * math.pi * score / total
            a0, a1 = within + gap / 2, within + piece - gap / 2
            if a1 <= a0:                      # a sliver too thin to gap
                a0, a1 = within, within + piece
            alpha = 1.0 - (1.0 - DONUT_MIN_ALPHA) * (depth / max(steps - 1, 1))
            wedges.append(
                f'<path class="wedge" d="M {cx + inner * math.cos(a0):.2f} '
                f'{cy + inner * math.sin(a0):.2f} '
                f'{_arc(cx, cy, outer, a0, a1)} '
                f'L {cx + inner * math.cos(a1):.2f} {cy + inner * math.sin(a1):.2f} '
                f'A {inner:.2f} {inner:.2f} 0 0 0 {cx + inner * math.cos(a0):.2f} '
                f'{cy + inner * math.sin(a0):.2f} Z" '
                f'fill="{fill}" fill-opacity="{alpha:.2f}" '
                f'data-asset="{escape(asset_id)}" data-name="{escape(name)}" '
                f'data-category="{escape(category)}" data-value="{score:.1f}">'
                f'<title>{escape(name)} — {escape(category)} · '
                f'{_fmt(score)}</title></path>'
            )
            within += piece
        # One direct label a category, on the segment's midpoint. Named rather
        # than left to the colour: the contrast check warns at this surface, and
        # a legend of six hues beside a ring of six hues is the same information
        # twice.
        mid = angle + span / 2
        lx = cx + (outer + 10) * math.cos(mid)
        ly = cy + (outer + 10) * math.sin(mid)
        anchor = "start" if math.cos(mid) > 0.02 else (
            "end" if math.cos(mid) < -0.02 else "middle")
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            f'font-size="10" fill="var(--text-secondary)" '
            f'dominant-baseline="middle">'
            f'{escape(_fit(category, DONUT_LABEL_CHARS))}</text>'
        )
        rows.append((category, share, fill))
        angle += span

    # In the same order the ring goes, clockwise from twelve, so a row is
    # found by position rather than by matching a swatch to a hue. That is what
    # keeps the figure readable for the pairs the palette separates least: no
    # one has to tell the pink from the orange to read this.
    share_rows = "".join(
        f"<tr><td><i class='swatch' style='background: {fill}'></i>"
        f"{escape(c)}</td><td class='num'>{v:,.1f}</td>"
        f"<td class='num'>{100 * v / total:.0f}%</td></tr>"
        for c, v, fill in rows
    )
    return (
        f'<div class="chart donut">'
        f'<svg viewBox="0 {-DONUT_VPAD} {size + 2 * DONUT_GUTTER} '
        f'{size + 2 * DONUT_VPAD}" class="donutchart" '
        f'role="img" '
        f'aria-label="Share of the counting total by roster category" '
        f'data-chart="{chart_id}">'
        f'<g transform="translate({DONUT_GUTTER},0)">'
        f'{"".join(wedges)}{"".join(labels)}'
        f'<text x="{cx}" y="{cy - 4}" text-anchor="middle" font-size="22" '
        f'font-weight="600" fill="var(--text-primary)">{total:,.1f}</text>'
        f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="10" '
        f'fill="var(--muted)">counting total</text></g></svg></div>'
        f'<table class="mixtable"><thead><tr><th>Category</th>'
        f'<th class="num">Points</th><th class="num">Share</th></tr></thead>'
        f'<tbody>{share_rows}</tbody></table>'
    )


def slot_sections(
    rows: list[tuple[str, str, str]],
    managers: list[tuple[str, int]],
    values: dict[tuple[str, str], tuple[float, str, str]],
    depth: dict[str, int] | None = None,
    width: int = 1000,
) -> str:
    """One collapsible section per league, each with its own chart.

    Twenty leagues open at once is a page that is long before it is
    informative. ``<details>`` collapses without script and keeps working with
    JavaScript off, which is what a static site wants; the script only
    remembers which sections a reader left open, because a reader who collapses
    fifteen leagues does not want to do it again tomorrow.

    Each section draws its own chart rather than one chart being cut up, so a
    collapsed league costs nothing to lay out and every chart is still on the
    same 0-100 scale -- the ceiling is computed across all of them, not per
    section, or two sections could not be compared.
    """
    if not rows or not managers:
        return '<p class="sub">Nothing scored yet.</p>'

    categories: list[str] = []
    for category, _, _ in rows:
        if category not in categories:
            categories.append(category)
    # One ceiling across every section: a per-section ceiling would make two
    # sections' bars look alike at different scores.
    top = _nice_ceiling(max((v[0] for v in values.values()), default=1))

    parts = []
    for category in categories:
        block = [r for r in rows if r[0] == category]
        held = sum(
            1 for _, key, _ in block for manager, _ in managers
            if (manager, key) in values
        )
        chart = contribution_chart(
            block, managers, values, width=width,
            chart_id=f"slots-{_slug(category)}", depth=depth, top=top,
        )
        parts.append(
            f'<details class="leaguebox" data-league="{escape(category)}" open>'
            f'<summary><span class="name">{escape(category)}</span>'
            f'<span class="count">{held} slot{"" if held == 1 else "s"}</span>'
            f'</summary>{chart}</details>'
        )
    return "".join(parts)


def _slug(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in str(text)).strip("-")


def legend(managers: list[tuple[str, int]], filterable: bool = False) -> str:
    """Always present for two or more series, so identity is never colour alone.

    ``filterable`` makes each entry a button that hides that manager across the
    bars, the tables and the progression line together, because a reader
    filtering one means all three. Rendered as real buttons so the filter is
    reachable from a keyboard; with no script they are inert and the key still
    reads as a key.
    """
    if not filterable:
        items = "".join(
            f'<span><i class="swatch" style="background: var(--series-{slot})"></i>'
            f'{escape(name)}</span>'
            for name, slot in managers
        )
        return f'<div class="legend">{items}</div>'

    items = "".join(
        f'<button type="button" class="legenditem" data-manager="{escape(name)}" '
        f'aria-pressed="false">'
        f'<i class="swatch" style="background: var(--series-{slot})"></i>'
        f'{escape(name)}</button>'
        for name, slot in managers
    )
    return f'<div class="legend filterable">{items}</div>'


SCRIPT = """\
// Crosshair and tooltip for the progression chart. An HTML chart is
// interactive by default; the table view below it is what makes every value
// readable without one.
(function () {
  var tip = document.createElement('div');
  tip.className = 'tooltip';
  document.body.appendChild(tip);

  document.querySelectorAll('svg.linechart').forEach(function (svg) {
    var holder = svg.closest('.chart');
    var node = holder && holder.parentNode.querySelector('script.chartdata');
    if (!node) return;
    var data = JSON.parse(node.textContent);
    var cross = svg.querySelector('.crosshair');
    var box = svg.querySelector('.hitbox');
    var steps = Math.max(data.days.length - 1, 1);

    function at(event) {
      var rect = svg.getBoundingClientRect();
      var scale = data.padLeft + data.plotW ? rect.width / svg.viewBox.baseVal.width : 1;
      var localX = (event.clientX - rect.left) / scale;
      var ratio = (localX - data.padLeft) / data.plotW;
      return Math.max(0, Math.min(steps, Math.round(ratio * steps)));
    }

    function show(event) {
      var i = at(event);
      var x = data.padLeft + data.plotW * i / steps;
      cross.setAttribute('x1', x); cross.setAttribute('x2', x);
      cross.setAttribute('opacity', '1');

      var rows = data.series.slice().sort(function (a, b) {
        return b.values[i] - a.values[i];
      }).map(function (s) {
        return '<div class="tt-row"><i class="swatch" style="background: var(--series-' +
          s.slot + ')"></i>' + s.name + '<b>' +
          s.values[i].toLocaleString(undefined, {maximumFractionDigits: 0}) + '</b></div>';
      }).join('');
      tip.innerHTML = '<div class="tt-date">' + data.days[i] + '</div>' + rows;
      tip.style.opacity = '1';
      var pad = 14;
      var left = event.pageX + pad;
      if (left + tip.offsetWidth > window.scrollX + document.documentElement.clientWidth) {
        left = event.pageX - tip.offsetWidth - pad;
      }
      tip.style.left = left + 'px';
      tip.style.top = (event.pageY - tip.offsetHeight / 2) + 'px';
    }

    // The hit target is the whole plot, not the 2px line -- landing on a
    // stroke dead-centre is not a reasonable thing to ask of a reader.
    box.addEventListener('mousemove', show);
    box.addEventListener('mouseleave', function () {
      tip.style.opacity = '0';
      cross.setAttribute('opacity', '0');
    });
  });

  // --- filtering by manager, and remembering collapsed leagues ---------
  // Hiding a series is a class, not a redraw: the axis must not move when a
  // manager is hidden, because a rescaling axis makes the remaining lines
  // appear to move as well.
  var hidden = {};
  function applyFilter() {
    document.querySelectorAll(
      'svg [data-manager]'
    ).forEach(function (node) {
      node.classList.toggle('ghosted', !!hidden[node.dataset.manager]);
    });
    document.querySelectorAll('table[data-columns]').forEach(function (table) {
      var names = JSON.parse(table.dataset.columns);
      names.forEach(function (name, index) {
        var off = !!hidden[name];
        table.querySelectorAll('tr').forEach(function (row) {
          var cell = row.children[index + 1];
          if (cell) cell.classList.toggle('ghosted', off);
        });
      });
    });
  }
  document.querySelectorAll('.legend.filterable .legenditem').forEach(function (item) {
    item.addEventListener('click', function () {
      var name = item.dataset.manager;
      hidden[name] = !hidden[name];
      document.querySelectorAll(
        '.legend.filterable .legenditem[data-manager="' + name.replace(/"/g, '\\"') + '"]'
      ).forEach(function (twin) {
        twin.setAttribute('aria-pressed', hidden[name] ? 'true' : 'false');
        twin.classList.toggle('off', !!hidden[name]);
      });
      applyFilter();
    });
  });

  // --- filtering the results table -------------------------------------
  // Two independent sets of chips. Within a set the chosen values are OR-ed --
  // picking two leagues means either -- and the sets are AND-ed together, so
  // "Player" plus two leagues is the players in those two. Nothing chosen in a
  // set means that set is not filtering, which is what makes the page useful
  // before anything is clicked.
  var picked = {kind: {}, league: {}, scoring: {}};
  function anyPicked(set) {
    for (var k in set) if (set[k]) return true;
    return false;
  }
  function applyResultsFilter() {
    var table = document.getElementById('resultstable');
    if (!table) return;
    var shown = 0;
    table.querySelectorAll('tbody tr').forEach(function (row) {
      var ok = true;
      ['kind', 'league'].forEach(function (name) {
        if (anyPicked(picked[name]) && !picked[name][row.dataset[name]]) ok = false;
      });
      if (picked.scoring.yes) {
        var cell = row.querySelector('[data-score]');
        if (!cell || Number(cell.dataset.score) <= 0) ok = false;
      }
      row.hidden = !ok;
      if (ok) shown++;
    });
    var count = document.querySelector('[data-count]');
    if (count) {
      var total = table.querySelectorAll('tbody tr').length;
      count.textContent = shown === total
        ? 'Showing every scored asset.'
        : 'Showing ' + shown + ' of ' + total + '.';
    }
  }
  document.querySelectorAll('.chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      var set = picked[chip.dataset.filter];
      var value = chip.dataset.value;
      set[value] = !set[value];
      chip.setAttribute('aria-pressed', set[value] ? 'true' : 'false');
      applyResultsFilter();
    });
  });

  // Which figures a reader left closed, alongside the league boxes below. A
  // page of three figures is long, and someone who wants only the table should
  // not have to scroll past two charts every visit.
  var FKEY = 'whul.figures';
  var shut = {};
  try { shut = JSON.parse(localStorage.getItem(FKEY) || '{}'); } catch (e) {}
  document.querySelectorAll('details.figure').forEach(function (box) {
    var name = box.dataset.figure;
    // An anchor wins over a remembered state: arriving at #everyone and
    // finding it closed is the one case where the memory is wrong.
    if (shut[name] && location.hash !== '#' + name) box.open = false;
    box.addEventListener('toggle', function () {
      shut[name] = !box.open;
      try { localStorage.setItem(FKEY, JSON.stringify(shut)); } catch (e) {}
    });
  });
  window.addEventListener('hashchange', function () {
    var target = document.querySelector(location.hash || '#none');
    if (target && target.classList.contains('figure')) target.open = true;
  });

  // Which leagues a reader left collapsed. Someone who closes fifteen of them
  // does not want to do it again tomorrow.
  var KEY = 'whul.collapsed';
  var collapsed = {};
  try { collapsed = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
  document.querySelectorAll('details.leaguebox').forEach(function (box) {
    var name = box.dataset.league;
    if (collapsed[name]) box.open = false;
    box.addEventListener('toggle', function () {
      collapsed[name] = !box.open;
      try { localStorage.setItem(KEY, JSON.stringify(collapsed)); } catch (e) {}
    });
  });

  document.querySelectorAll('svg.barchart .bar').forEach(function (bar) {
    bar.addEventListener('mousemove', function (event) {
      // The name leads. This tooltip overrides the <title> the SVG carries,
      // so a title that names the asset is no help here -- it showed the slot
      // key, "Club Soccer Top 3 1", which says nothing about who the bar is.
      var name = bar.dataset.name || bar.dataset.category;
      var kind = bar.dataset.kind ? ' · ' + bar.dataset.kind : '';
      tip.innerHTML = '<div class="tt-date">' + name + kind + '</div>' +
        '<div class="tt-row">' + bar.dataset.manager + '<b>' +
        Number(bar.dataset.value).toLocaleString(undefined, {maximumFractionDigits: 1}) +
        '</b></div>';
      tip.style.opacity = '1';
      tip.style.left = (event.pageX + 14) + 'px';
      tip.style.top = (event.pageY - tip.offsetHeight / 2) + 'px';
    });
    bar.addEventListener('mouseleave', function () { tip.style.opacity = '0'; });
  });

  // A wedge's alpha says which holding inside a category it is, and alpha is
  // not readable as a quantity -- so the wedge has to say who it is on hover.
  // Without this the ring shows six categories and nothing else; the names are
  // the point of splitting them at all.
  document.querySelectorAll('svg.donutchart .wedge').forEach(function (wedge) {
    wedge.addEventListener('mousemove', function (event) {
      tip.innerHTML = '<div class="tt-date">' + wedge.dataset.name + '</div>' +
        '<div class="tt-row">' + wedge.dataset.category + '<b>' +
        Number(wedge.dataset.value).toLocaleString(undefined, {maximumFractionDigits: 1}) +
        '</b></div>';
      tip.style.opacity = '1';
      tip.style.left = (event.pageX + 14) + 'px';
      tip.style.top = (event.pageY - tip.offsetHeight / 2) + 'px';
    });
    wedge.addEventListener('mouseleave', function () { tip.style.opacity = '0'; });
  });

  // --- the profile window ---------------------------------------------
  // Every asset the page mentions ships with it, so opening a profile is a
  // local lookup rather than a request. On a static site there is nothing to
  // request from.
  var node = document.getElementById('assetdata');
  if (!node) return;
  var profiles = JSON.parse(node.textContent);
  var dialog = document.getElementById('profile');
  if (!dialog) return;

  function open(id) {
    var a = profiles[id];
    if (!a) return;
    var stats = (a.stats || []).map(function (row) {
      return '<tr><td>' + row[0] + '</td><td class="num">' + row[1] + '</td></tr>';
    }).join('');
    // Every finish, newest first. A total says how much; this says what
    // happened, which is what a profile is opened for.
    var finishes = (a.finishes || []).map(function (f) {
      return '<tr><td>' + f.label + '</td>' +
             '<td class="when">' + (f.date || '') + '</td>' +
             '<td class="num">' + f.points.toLocaleString() + '</td></tr>';
    }).join('');
    // A prorated or schedule-scaled figure looks like an ordinary one, and a
    // manager checking it against a box score would find it does not
    // reconcile. Saying so is cheaper than being asked.
    var notes = (a.notes || []).map(function (n) {
      return '<p class="note">' + n + '</p>';
    }).join('');
    dialog.innerHTML =
      '<button class="close" aria-label="Close">&times;</button>' +
      '<div class="head">' + a.avatar +
        '<div><div class="nm">' + a.name + (a.badge || '') + '</div>' +
        '<div class="meta">' + a.meta + '</div></div></div>' +
      (finishes
        ? '<div class="body"><h3>Finishes</h3><table class="finishes"><tbody>' +
          finishes + '</tbody></table></div>'
        : '') +
      (stats ? '<div class="body">' + (finishes ? '<h3>Season totals</h3>' : '') +
               '<table><tbody>' + stats + '</tbody></table></div>'
             : '<div class="body"><p class="sub">No stat lines recorded for this ' +
               'day yet.</p></div>') +
      (notes ? '<div class="body">' + notes + '</div>' : '') +
      '<div class="scoreline">' +
        '<div><div class="label">Raw score</div><div class="value">' + a.raw + '</div></div>' +
        '<div><div class="label">Normalized</div><div class="value">' + a.scaled + '</div></div>' +
      '</div>';
    dialog.querySelector('.close').addEventListener('click', function () {
      dialog.close();
    });
    tip.style.opacity = '0';
    dialog.showModal();
  }

  document.querySelectorAll('svg.barchart .bar').forEach(function (bar) {
    if (!bar.dataset.asset) return;
    bar.addEventListener('click', function () { open(bar.dataset.asset); });
  });
  document.querySelectorAll('svg.donutchart .wedge').forEach(function (wedge) {
    if (!wedge.dataset.asset) return;
    wedge.addEventListener('click', function () { open(wedge.dataset.asset); });
  });
  document.querySelectorAll('button.assetlink').forEach(function (button) {
    button.addEventListener('click', function () { open(button.dataset.asset); });
  });
  // Clicking the backdrop closes it, which is what the click outside a modal
  // is for; the dialog element does not do this on its own.
  dialog.addEventListener('click', function (event) {
    if (event.target === dialog) dialog.close();
  });
})();
"""
