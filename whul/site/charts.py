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
            f'stroke-width="{LINE_WIDTH}" stroke-linejoin="round" stroke-linecap="round"/>'
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
            f'fill="{entry.color}" stroke="var(--surface-1)" stroke-width="{SURFACE_RING}"/>'
        )
        label_y = ey if previous is None else max(ey, previous + min_gap)
        previous = label_y
        # A leader line where the label had to move, so it still reads as
        # belonging to its own series rather than to whatever it now sits by.
        if label_y - ey > 2:
            parts.append(
                f'<path d="M{ex + 5:.1f},{ey:.1f} L{ex + 8:.1f},{label_y:.1f}" '
                f'stroke="{entry.color}" stroke-width="1" fill="none" opacity="0.6"/>'
            )
        parts.append(
            f'<text x="{ex + 11:.1f}" y="{label_y + 4:.1f}" font-size="12" '
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
SLOT_ROW_PAD = 9


def contribution_chart(
    rows: list[tuple[str, str, str]],
    managers: list[tuple[str, int]],
    values: dict[tuple[str, str], tuple[float, str, str]],
    width: int = 1000,
    chart_id: str = "contribution",
    depth: dict[str, int] | None = None,
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

    pad_left, pad_right, pad_top = 150, 56, 8
    def _row_height(category: str) -> float:
        bars = len(managers) * per_row[category]
        return bars * (SLOT_BAR_THICKNESS + BAR_GAP) + SLOT_ROW_PAD

    heights = {c: _row_height(c) for c in categories}
    plot_h = sum(heights.values())
    height = pad_top + plot_h + 28
    plot_w = width - pad_left - pad_right
    top = _nice_ceiling(max((v[0] for v in values.values()), default=1))

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
        base_y = offset + 4
        parts.append(
            f'<text x="{pad_left - 10}" y="{base_y + 10:.1f}" text-anchor="end" '
            f'font-size="12" fill="var(--text-primary)" font-weight="600">'
            f'{escape(category)}</text>'
        )
        parts.append(
            f'<line x1="{pad_left - 4}" y1="{base_y - 3:.1f}" '
            f'x2="{width - pad_right}" y2="{base_y - 3:.1f}" '
            f'stroke="var(--grid)" stroke-width="1"/>'
        )
        index = 0
        for manager, slot in managers:
            for rank in range(1, per_row[category] + 1):
                key = f"{category} {rank}"
                score, asset_id, asset_name = values.get((manager, key), (0.0, "", ""))
                bar_y = base_y + index * (SLOT_BAR_THICKNESS + BAR_GAP) + 2
                index += 1
                # Which of the manager's slots this is. Colour says whose the
                # bar is; without this nothing says whether it is their best
                # holding in the category or their fourth.
                if per_row[category] > 1:
                    parts.append(
                        f'<text x="{pad_left - 10}" '
                        f'y="{bar_y + SLOT_BAR_THICKNESS - 3:.1f}" text-anchor="end" '
                        f'font-size="10" fill="var(--muted)">#{rank}</text>'
                    )
                bar_w = max(plot_w * score / top, 0.0)
                radius = min(BAR_RADIUS, bar_w / 2) if bar_w else 0
                parts.append(
                    f'<rect class="bar" x="{pad_left}" y="{bar_y:.1f}" '
                    f'width="{bar_w:.1f}" height="{SLOT_BAR_THICKNESS}" '
                    f'rx="{radius:.1f}" fill="var(--series-{slot})" '
                    # The title carries the fully qualified key: "#2" means
                    # nothing read on its own, and a screen reader gets no help
                    # from the visual grouping.
                    f'data-manager="{escape(manager)}" data-category="{escape(key)}" '
                    f'data-asset="{escape(asset_id)}" '
                    f'data-value="{score:.1f}"><title>{escape(manager)} — '
                    f'{escape(key)}: {escape(asset_name)} {_fmt(score)}</title></rect>'
                )
        offset += heights[category]
    parts.append("</svg>")
    return '<div class="chart">' + "".join(parts) + "</div>"


def legend(managers: list[tuple[str, int]]) -> str:
    """Always present for two or more series, so identity is never colour alone."""
    items = "".join(
        f'<span><i class="swatch" style="background: var(--series-{slot})"></i>'
        f'{escape(name)}</span>'
        for name, slot in managers
    )
    return f'<div class="legend">{items}</div>'


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

  document.querySelectorAll('svg.barchart .bar').forEach(function (bar) {
    bar.addEventListener('mousemove', function (event) {
      tip.innerHTML = '<div class="tt-date">' + bar.dataset.category + '</div>' +
        '<div class="tt-row">' + bar.dataset.manager + '<b>' +
        Number(bar.dataset.value).toLocaleString(undefined, {maximumFractionDigits: 0}) +
        '</b></div>';
      tip.style.opacity = '1';
      tip.style.left = (event.pageX + 14) + 'px';
      tip.style.top = (event.pageY - tip.offsetHeight / 2) + 'px';
    });
    bar.addEventListener('mouseleave', function () { tip.style.opacity = '0'; });
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
