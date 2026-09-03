"""The admin tool: a local page for the things a static site cannot do.

The published site is files. Files cannot accept a trade, so the admin lives
here instead -- a small server run on the league admin's own machine, against
the same database the nightly job reads. Nothing about it is reachable from the
published site, which is the point: the league's pages are public, and the
controls that change them should not be.

Built on the standard library's HTTP server. A five-manager league does not
justify a web framework, and a tool with no dependencies still runs in a year.

    python -m whul.cli admin

Trades are the first thing it does because they are the only routine change
that cannot wait for a rebuild: a trade has an effective date, and every day
after it is scored wrongly until it is recorded.
"""

from __future__ import annotations

import json
import webbrowser
from datetime import date
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from whul.site import theme
from whul.store import rosters
from whul.store.db import Store

#: Loopback only. Binding to anything else would put write access to the
#: league on the network, which is exactly what this design avoids.
HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def _rows(frame) -> list[dict]:
    """Records with real ``None`` where the query found nothing.

    ``DataFrame.where(notna(), None)`` leaves NaN in place for object and float
    columns, and NaN is truthy -- so an unfilled slot would read as filled, pass
    the "is there anything to trade" check, and fail inside the write instead.
    """
    import math

    def clean(value):
        if value is None:
            return None
        if isinstance(value, float) and math.isnan(value):
            return None
        return value

    return [{k: clean(v) for k, v in row.items()} for row in frame.to_dict("records")]


def open_slots(store: Store, season: str) -> list[dict]:
    """Every slot with its current occupant, for the trade form.

    A slot whose occupant was released still appears, with no asset. It is a
    slot that needs filling, which is exactly when it should be visible.
    """
    rows = store.query(
        "SELECT s.slot_id, s.manager_id, s.category, s.asset_type, s.slot_index, "
        "       o.asset_id, a.display_name "
        "FROM roster_slots s "
        "LEFT JOIN slot_occupancy o "
        "  ON o.slot_id = s.slot_id AND o.end_date IS NULL "
        "LEFT JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE s.season = ? "
        "ORDER BY s.manager_id, s.asset_type, s.category, s.slot_index",
        (season,),
    )
    return _rows(rows)


def trade_history(store: Store, season: str, limit: int = 25) -> list[dict]:
    """Recorded trades, most recent first, so a mistake is visible."""
    rows = store.query(
        "SELECT o.slot_id, s.manager_id, s.category, o.asset_id, a.display_name, "
        "       o.start_date, o.note "
        "FROM slot_occupancy o "
        "JOIN roster_slots s ON s.slot_id = o.slot_id "
        "LEFT JOIN assets a ON a.asset_id = o.asset_id "
        "WHERE s.season = ? AND o.note LIKE '%trade%' "
        "ORDER BY o.start_date DESC, o.slot_id LIMIT ?",
        (season, limit),
    )
    return _rows(rows)


def apply_trade(
    store: Store,
    season: str,
    left_slot: str,
    right_slot: str,
    effective: str,
    note: str = "",
) -> dict:
    """Record a reciprocal swap and say what changed.

    Validated before anything is written. A trade recorded against the wrong
    slot is worse than one not recorded at all: the second is noticed, the
    first quietly rescores every day after its effective date.
    """
    slots = {s["slot_id"]: s for s in open_slots(store, season)}
    problems = []
    for slot_id in (left_slot, right_slot):
        if slot_id not in slots:
            problems.append(f"no slot {slot_id!r} in {season}")
        elif not slots[slot_id]["asset_id"]:
            problems.append(f"slot {slot_id!r} is empty; there is nothing to trade")
    if left_slot == right_slot:
        problems.append("both sides are the same slot")
    if not problems:
        left, right = slots[left_slot], slots[right_slot]
        if left["manager_id"] == right["manager_id"]:
            problems.append("both slots belong to the same manager")
        if (left["asset_type"], left["category"]) != (right["asset_type"], right["category"]):
            problems.append(
                f"{left['asset_type']}/{left['category']} cannot swap with "
                f"{right['asset_type']}/{right['category']} -- a slot only holds "
                f"its own category"
            )
    try:
        when = date.fromisoformat(effective)
    except ValueError:
        problems.append(f"{effective!r} is not a date")
        when = None

    if problems:
        return {"ok": False, "problems": problems}

    left, right = slots[left_slot], slots[right_slot]
    rosters.trade(
        store, left_slot, right_slot,
        left["asset_id"], right["asset_id"], when,
        note=note or "trade",
    )
    overlaps = rosters.overlaps(store, season)
    return {
        "ok": True,
        "message": (
            f"{left['manager_id']} sends {left['display_name'] or left['asset_id']} "
            f"to {right['manager_id']} for {right['display_name'] or right['asset_id']}, "
            f"effective {when}."
        ),
        "warning": (
            f"{len(overlaps)} slot(s) now have overlapping occupancy"
            if not overlaps.empty else ""
        ),
        "rebuild": "python -m whul.cli rollup --backfill && python -m whul.cli site",
    }


def _page(store: Store, season: str, flash: dict | None = None) -> str:
    slots = open_slots(store, season)
    managers = sorted({s["manager_id"] for s in slots})
    filled = [s for s in slots if s["asset_id"]]

    def options(prefix: str) -> str:
        return "".join(
            f'<option value="{escape(s["slot_id"])}" '
            f'data-manager="{escape(s["manager_id"])}" '
            f'data-kind="{escape(s["asset_type"])}/{escape(s["category"])}">'
            f'{escape(s["manager_id"].title())} — {escape(s["category"])} '
            f'#{s["slot_index"]} — {escape(str(s["display_name"] or s["asset_id"]))}'
            f"</option>"
            for s in filled
        )

    banner = ""
    if flash:
        if flash.get("ok"):
            banner = (
                f'<div class="banner" style="border-left-color: var(--series-3)">'
                f'<strong>Recorded.</strong> {escape(flash["message"])}<br>'
                f'{escape(flash["warning"]) if flash.get("warning") else ""}'
                f'<br>Rebuild with <code>{escape(flash["rebuild"])}</code></div>'
            )
        else:
            banner = (
                '<div class="banner" style="border-left-color: var(--series-8)">'
                "<strong>Not recorded.</strong><ul>"
                + "".join(f"<li>{escape(p)}</li>" for p in flash["problems"])
                + "</ul></div>"
            )

    history = trade_history(store, season)
    rows = "".join(
        f"<tr><td>{escape(str(h['start_date']))}</td>"
        f"<td>{escape(str(h['manager_id']).title())}</td>"
        f"<td>{escape(str(h['category']))}</td>"
        f"<td>{escape(str(h['display_name'] or h['asset_id'] or ''))}</td></tr>"
        for h in history
    ) or "<tr><td colspan='4'>No trades recorded.</td></tr>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WHUL admin — {escape(season)}</title>
<style>{theme.STYLESHEET}
select, input {{ font: inherit; padding: 7px 9px; border: 1px solid var(--axis);
  border-radius: 6px; background: var(--surface-1); color: var(--text-primary);
  width: 100%; }}
label {{ display: block; font-size: 13px; color: var(--text-secondary);
  margin: 12px 0 4px; }}
button.go {{ font: inherit; font-weight: 600; padding: 9px 18px; margin-top: 18px;
  border: 0; border-radius: 6px; background: var(--series-1); color: #fff;
  cursor: pointer; }}
.kindnote {{ font-size: 12px; color: var(--muted); margin-top: 5px; min-height: 16px; }}
</style></head>
<body><div class="wrap">
<header class="masthead"><h1>WHUL admin</h1>
  <span class="stamp">{escape(season)} · local only</span></header>
{banner}
<div class="card">
  <h2>Record a trade</h2>
  <p class="sub">Reciprocal: the two slots swap occupants. The outgoing asset stops
    accruing the day before the effective date and the incoming one starts on it, so
    no day is counted twice and none is lost.</p>
  <form method="post" action="/trade">
    <label for="left">One side</label>
    <select id="left" name="left">{options('left')}</select>
    <label for="right">The other side</label>
    <select id="right" name="right">{options('right')}</select>
    <div class="kindnote" id="kindnote"></div>
    <label for="effective">Effective from</label>
    <input id="effective" name="effective" type="date" value="{date.today()}">
    <label for="note">Note (optional)</label>
    <input id="note" name="note" placeholder="e.g. two-for-one, second piece pending">
    <button class="go" type="submit">Record trade</button>
  </form>
</div>

<div class="card">
  <h2>Recorded trades</h2>
  <table><thead><tr><th>From</th><th>Manager</th><th>Category</th>
    <th>Received</th></tr></thead><tbody>{rows}</tbody></table>
</div>

<footer>{len(managers)} managers, {len(filled)} filled slots.
  This page is served on {HOST} and is not reachable from the published site.</footer>
</div>
<script>
// Say up front when two slots cannot swap, rather than after a submit.
var left = document.getElementById('left'), right = document.getElementById('right');
var note = document.getElementById('kindnote');
function check() {{
  var a = left.selectedOptions[0], b = right.selectedOptions[0];
  if (!a || !b) return;
  if (a.dataset.manager === b.dataset.manager) {{
    note.textContent = 'Both slots belong to the same manager.';
  }} else if (a.dataset.kind !== b.dataset.kind) {{
    note.textContent = a.dataset.kind + ' cannot swap with ' + b.dataset.kind + '.';
  }} else {{
    note.textContent = '';
  }}
}}
left.addEventListener('change', check); right.addEventListener('change', check); check();
</script>
</body></html>
"""


def serve(store: Store, season: str, port: int = DEFAULT_PORT, open_browser: bool = True):
    """Run the admin server until interrupted."""
    flash: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, status: int = 200) -> None:
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
            if urlparse(self.path).path not in ("/", "/index.html"):
                self._send("<h1>Not found</h1>", 404)
                return
            current = dict(flash)
            flash.clear()
            self._send(_page(store, season, current or None))

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/trade":
                self._send("<h1>Not found</h1>", 404)
                return
            length = int(self.headers.get("Content-Length", 0))
            form = parse_qs(self.rfile.read(length).decode())
            flash.clear()
            flash.update(apply_trade(
                store, season,
                form.get("left", [""])[0], form.get("right", [""])[0],
                form.get("effective", [""])[0], form.get("note", [""])[0],
            ))
            # Redirect after a write, so a refresh does not repeat the trade.
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, *args) -> None:
            pass  # the terminal is for the trade confirmations, not the requests

    server = ThreadingHTTPServer((HOST, port), Handler)
    url = f"http://{HOST}:{port}/"
    print(f"\nAdmin running at {url}")
    print("Local only -- nothing here is reachable from the published site.")
    print("Ctrl-C to stop.\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - a headless box has no browser, which is fine
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return server
