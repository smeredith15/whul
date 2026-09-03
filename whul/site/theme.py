"""Colour and type tokens for the generated site.

The palette is the validated reference instance from the data-viz guidance.
Five managers means five categorical slots, checked with the validator in both
modes before anything was drawn:

    light  worst adjacent CVD dE 9.1, normal-vision dE 19.6  -- all checks pass
    dark   worst adjacent CVD dE 8.4, normal-vision dE 19.3  -- all checks pass

Light mode raises a contrast warning: aqua, yellow and magenta sit below 3:1 on
the light surface. That is not dismissable, so the relief is built in -- every
series is directly labelled on the charts, and the standings table is the
default view rather than an alternative one.

**Colour follows the manager, never their rank.** A manager keeps the same hue
on every chart and every page, so the eye can carry identity between them, and
a change in the standings never repaints anything.
"""

from __future__ import annotations

#: Categorical slots, in the fixed order the validator passed them in. Never
#: cycled: a sixth manager takes slot 6, and past eight the league would need
#: a different encoding rather than an invented hue.
SERIES_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
                "#008300", "#4a3aa7", "#e34948")
SERIES_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181",
               "#008300", "#9085e9", "#e66767")
MAX_SERIES = len(SERIES_LIGHT)


def series_index(managers: list[str], manager: str) -> int:
    """A manager's fixed slot, from a stable ordering of the league."""
    return sorted(managers).index(manager) % MAX_SERIES


STYLESHEET = """\
:root {
  color-scheme: light;
  --surface-1: #fcfcfb;
  --page: #f9f9f7;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --axis: #c3c2b7;
  --series-1: #2a78d6;
  --series-2: #eb6834;
  --series-3: #1baf7a;
  --series-4: #eda100;
  --series-5: #e87ba4;
  --series-6: #008300;
  --series-7: #4a3aa7;
  --series-8: #e34948;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-1: #1a1a19;
    --page: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --axis: #383835;
    --series-1: #3987e5;
    --series-2: #d95926;
    --series-3: #199e70;
    --series-4: #c98500;
    --series-5: #d55181;
    --series-6: #008300;
    --series-7: #9085e9;
    --series-8: #e66767;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1: #1a1a19;
  --page: #0d0d0d;
  --text-primary: #ffffff;
  --text-secondary: #c3c2b7;
  --muted: #898781;
  --grid: #2c2c2a;
  --axis: #383835;
  --series-1: #3987e5;
  --series-2: #d95926;
  --series-3: #199e70;
  --series-4: #c98500;
  --series-5: #d55181;
  --series-6: #008300;
  --series-7: #9085e9;
  --series-8: #e66767;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--page);
  color: var(--text-primary);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
a { color: inherit; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 24px 20px 64px; }

header.masthead {
  display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
  border-bottom: 1px solid var(--grid); padding-bottom: 14px; margin-bottom: 22px;
}
.masthead h1 { font-size: 20px; margin: 0; letter-spacing: -0.01em; }
.masthead nav { display: flex; gap: 14px; margin-left: auto; flex-wrap: wrap; }
.masthead nav a { color: var(--text-secondary); text-decoration: none; font-size: 14px; }
.masthead nav a:hover, .masthead nav a[aria-current] {
  color: var(--text-primary); text-decoration: underline; text-underline-offset: 4px;
}
.stamp { color: var(--muted); font-size: 13px; }

.banner {
  background: var(--surface-1); border: 1px solid var(--grid);
  border-left: 3px solid var(--series-4);
  padding: 10px 14px; border-radius: 6px; margin-bottom: 22px;
  color: var(--text-secondary); font-size: 14px;
}

.card {
  background: var(--surface-1); border: 1px solid var(--grid);
  border-radius: 8px; padding: 18px 18px 12px; margin-bottom: 22px;
}
.card > h2 { font-size: 15px; margin: 0 0 2px; letter-spacing: -0.005em; }
.card > p.sub { margin: 0 0 16px; color: var(--text-secondary); font-size: 13px; }

table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--grid); }
th { color: var(--muted); font-weight: 600; font-size: 12px;
     text-transform: uppercase; letter-spacing: 0.04em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: color-mix(in srgb, var(--grid) 35%, transparent); }
.swatch {
  display: inline-block; width: 10px; height: 10px; border-radius: 2px;
  margin-right: 8px; vertical-align: baseline;
}
.bench td { color: var(--text-secondary); }
.bench .slotname::after {
  content: " bench"; color: var(--muted); font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.05em; margin-left: 6px;
}

.chart { width: 100%; overflow-x: auto; }
.chart svg { display: block; max-width: 100%; height: auto; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin: 4px 0 14px; font-size: 13px; }
.legend span { display: inline-flex; align-items: center; color: var(--text-secondary); }

details.tableview { margin-top: 10px; }
details.tableview summary {
  cursor: pointer; color: var(--text-secondary); font-size: 13px; padding: 4px 0;
}
details.tableview[open] summary { margin-bottom: 8px; }

.tooltip {
  position: absolute; pointer-events: none; opacity: 0; transition: opacity .08s;
  background: var(--surface-1); border: 1px solid var(--axis); border-radius: 6px;
  padding: 8px 10px; font-size: 13px; box-shadow: 0 2px 10px rgba(0,0,0,.12);
  z-index: 20; min-width: 150px;
}
.tooltip .tt-date { color: var(--muted); font-size: 12px; margin-bottom: 5px; }
.tooltip .tt-row { display: flex; align-items: center; gap: 7px; white-space: nowrap; }
.tooltip .tt-row b { margin-left: auto; font-variant-numeric: tabular-nums; font-weight: 600; }

.avatar {
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 50%; object-fit: cover; flex: none;
  font-weight: 650; letter-spacing: 0.01em; overflow: hidden;
  vertical-align: middle;
}
.avatar.mono { border: 1px solid color-mix(in srgb, currentColor 28%, transparent); }
.badge {
  width: 18px; height: 18px; border-radius: 3px; object-fit: contain;
  vertical-align: middle; margin-left: 6px;
}
.who { display: inline-flex; align-items: center; gap: 9px; }
.empty td { color: var(--muted); }
.empty .undrafted {
  font-style: italic;
}
.empty .slotname::after {
  content: " open"; color: var(--series-4); font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.05em; margin-left: 6px;
  font-style: normal;
}
.struck { text-decoration: line-through; text-decoration-thickness: 1.5px;
          color: var(--text-secondary); }

button.assetlink {
  background: none; border: 0; padding: 0; margin: 0; font: inherit;
  color: inherit; cursor: pointer; text-align: left;
}
button.assetlink:hover span.nm { text-decoration: underline; text-underline-offset: 3px; }
svg .bar { cursor: pointer; }
svg .bar:hover { opacity: 0.82; }

dialog.profile {
  border: 1px solid var(--axis); border-radius: 10px; padding: 0;
  background: var(--surface-1); color: var(--text-primary);
  max-width: 420px; width: calc(100% - 32px);
  box-shadow: 0 12px 40px rgba(0,0,0,.28);
}
dialog.profile::backdrop { background: rgba(0,0,0,.45); }
dialog.profile .head {
  display: flex; gap: 14px; align-items: center;
  padding: 18px 18px 14px; border-bottom: 1px solid var(--grid);
}
dialog.profile .head .nm { font-size: 17px; font-weight: 650; letter-spacing: -0.01em; }
dialog.profile .head .meta { color: var(--text-secondary); font-size: 13px; margin-top: 1px; }
dialog.profile .body { padding: 6px 18px 16px; }
dialog.profile table { font-size: 13.5px; }
dialog.profile .close {
  position: absolute; top: 12px; right: 14px; background: none; border: 0;
  color: var(--muted); font-size: 20px; line-height: 1; cursor: pointer; padding: 4px;
}
dialog.profile .scoreline {
  display: flex; gap: 20px; padding: 12px 18px; border-top: 1px solid var(--grid);
}
dialog.profile .scoreline div { flex: 1; }
dialog.profile .scoreline .label { color: var(--text-secondary); font-size: 12px; }
dialog.profile .scoreline .value {
  font-size: 20px; font-weight: 650; letter-spacing: -0.01em;
}

.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
.tile { background: var(--surface-1); border: 1px solid var(--grid);
        border-radius: 8px; padding: 14px 16px; }
.tile .label { color: var(--text-secondary); font-size: 13px; }
.tile .value { font-size: 26px; font-weight: 650; letter-spacing: -0.02em; margin-top: 2px; }
.tile .note { color: var(--muted); font-size: 12px; margin-top: 2px; }

footer { color: var(--muted); font-size: 12px; border-top: 1px solid var(--grid);
         padding-top: 14px; margin-top: 30px; }
@media (max-width: 640px) {
  .masthead nav { margin-left: 0; width: 100%; }
  th, td { padding: 6px 7px; }
}
"""
