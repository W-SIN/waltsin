#!/usr/bin/env python3
"""Local test harness: exercises generate_live_section.py against a sample
.ics file WITHOUT hitting the network, and without touching index.html.
Prints the three generated blocks + the classification counts.
"""
import os
import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import generate_live_section as g

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

tz = ZoneInfo("Australia/Sydney")
now = datetime.now(tz)
window_start = now - relativedelta(months=15)
window_end = now + relativedelta(months=24)

with open(os.path.join(os.path.dirname(__file__), "sample.ics"), "rb") as f:
    ics_bytes = f.read()

events = g.load_events(ics_bytes, tz, window_start, window_end)

highlights = sorted((e for e in events if e["is_highlight"]), key=lambda e: e["sort_dt"], reverse=True)
shows = [e for e in events if not e["is_highlight"]]
upcoming = sorted((e for e in shows if e["sort_dt"] >= now), key=lambda e: e["sort_dt"])
past_cutoff = now - relativedelta(months=12)
past = sorted(
    (e for e in shows if e["sort_dt"] < now and e["sort_dt"] >= past_cutoff),
    key=lambda e: e["sort_dt"], reverse=True,
)
excluded_old = [e for e in shows if e["sort_dt"] < past_cutoff]

print(f"now = {now.isoformat()}")
print(f"counts -> upcoming={len(upcoming)} past={len(past)} highlights={len(highlights)} excluded(older than 12mo)={len(excluded_old)}")
print()
print("=== UPCOMING ===")
print(g.render_upcoming_block(upcoming))
print()
print("=== PAST (12mo cap) ===")
print(g.render_past_block(past))
print()
print("=== HIGHLIGHTS ===")
print(g.render_highlights_block(highlights))
