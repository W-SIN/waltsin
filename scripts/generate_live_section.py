#!/usr/bin/env python3
"""
generate_live_section.py

Pulls events from a (secret) Google Calendar iCal feed and regenerates three
blocks of content inside index.html, between HTML comment markers:

  <!-- LIVE:UPCOMING:START -->   ... <!-- LIVE:UPCOMING:END -->
      Upcoming shows, soonest first. show-item / show-date / show-venue /
      show-details / show-context / show-ticket-link markup.

  <!-- LIVE:PAST:START -->       ... <!-- LIVE:PAST:END -->
      Past shows, most recent first, capped to the last N months
      (default 12). Same show-item markup, lives inside a collapsed
      <details class="past-shows"> in index.html.

  <!-- LIVE:HIGHLIGHTS:START --> ... <!-- LIVE:HIGHLIGHTS:END -->
      "Highlights" reel: any PAST event whose description ends with a
      trailing "#" (after trimming whitespace) is pulled OUT of Past
      Shows and rendered here instead, most recent first, uncapped.
      The trailing "#" is stripped before the text is used anywhere.

      IMPORTANT: date takes precedence over the tag. An event tagged "#"
      that hasn't happened yet still shows normally in Live/Upcoming --
      it only gets reclassified into Highlights the first time the
      script runs *after* its date has passed. This means you can tag
      an event the moment you create it and never touch it again.

CALENDAR EVENT CONVENTIONS (fill these in when creating an event):
  Title (SUMMARY)   -> venue / event name           (required)
  Location          -> address / suburb             (optional)
  Start/end time    -> used to build the time range, e.g. "6:00-8:00pm"
                       (all-day events just show the date, no time)
  Description       -> free text. Two special bits are recognised:
                          - a line "Tickets: <url>"   -> becomes the
                            ticket button (any text before/after the URL
                            on that line is ignored)
                          - a trailing "#" at the very end of the
                            description -> marks this event to become a
                            "Highlight" once its date has passed
                       Whatever description text is left over (after
                       removing the Tickets line and trailing #) is shown
                       as the italic show-context line for shows, or as
                       the highlight-details line for highlights (falling
                       back to Location if the description is empty).

Usage:
  python scripts/generate_live_section.py \
      --ical-url "$ICAL_URL" \
      --html-path index.html

  ICAL_URL can also be supplied via the ICAL_URL environment variable.

Requires: requests, icalendar, recurring-ical-events, python-dateutil
(see scripts/requirements.txt)
"""

import argparse
import html
import os
import re
import sys
from datetime import datetime, date, time, timedelta

import requests
from dateutil.relativedelta import relativedelta
from icalendar import Calendar
import recurring_ical_events

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9 fallback
    from backports.zoneinfo import ZoneInfo  # type: ignore

MARKER_NAMES = ("UPCOMING", "PAST", "HIGHLIGHTS")
TICKET_RE = re.compile(r"^[ \t]*tickets?[ \t]*:[ \t]*(\S+)[ \t]*$", re.IGNORECASE | re.MULTILINE)
SEP = " &nbsp;·&nbsp; "  # matches existing " &nbsp;·&nbsp; " separator style


# ---------------------------------------------------------------------------
# Fetching & parsing
# ---------------------------------------------------------------------------

def fetch_ics(url: str) -> bytes:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content


def to_local(dt, tz):
    """Return a tz-aware datetime in `tz` for either a date or datetime."""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=tz)
        return dt.astimezone(tz)
    # plain date (all-day event)
    return datetime.combine(dt, time.min, tzinfo=tz)


def parse_description(raw: str):
    """Returns (ticket_url, remaining_text, is_highlight_tagged)."""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    trimmed_full = text.rstrip()
    is_highlight_tagged = trimmed_full.endswith("#")
    text = trimmed_full[:-1] if is_highlight_tagged else trimmed_full

    ticket_url = None
    m = TICKET_RE.search(text)
    if m:
        ticket_url = m.group(1)
        text = text[: m.start()] + text[m.end():]

    remaining = re.sub(r"\n{2,}", "\n", text).strip()
    return ticket_url, remaining, is_highlight_tagged


def load_events(ics_bytes: bytes, tz, window_start: datetime, window_end: datetime):
    cal = Calendar.from_ical(ics_bytes)
    raw_events = recurring_ical_events.of(cal).between(window_start, window_end)

    events = []
    for comp in raw_events:
        dtstart = comp.get("dtstart").dt
        dtend_field = comp.get("dtend")
        dtend = dtend_field.dt if dtend_field else None
        all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)

        start_local = to_local(dtstart, tz)
        end_local = to_local(dtend, tz) if dtend is not None else None

        # iCal all-day DTEND is exclusive (day after the last day) - adjust
        # so multi-day festival-style entries display their real last day.
        end_date_display = None
        if all_day and dtend is not None:
            last_day = dtend - timedelta(days=1)
            if last_day > dtstart:
                end_date_display = last_day

        summary = str(comp.get("summary", "") or "").strip()
        location = str(comp.get("location", "") or "").strip()
        description_raw = str(comp.get("description", "") or "")
        ticket_url, context, is_highlight_tagged = parse_description(description_raw)

        events.append({
            "summary": summary,
            "location": location,
            "all_day": all_day,
            "start_local": start_local,
            "end_local": end_local,
            "start_date": dtstart if all_day else start_local.date(),
            "end_date_display": end_date_display,
            "sort_dt": start_local,
            "ticket_url": ticket_url,
            "context": context,
            "is_highlight_tagged": is_highlight_tagged,
        })
    return events


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def esc(text: str, quote: bool = False) -> str:
    return html.escape(text or "", quote=quote)


def format_date_range(ev) -> str:
    start_d = ev["start_date"]
    end_d = ev["end_date_display"]
    if not end_d:
        return start_d.strftime("%a %-d %B %Y")
    if start_d.year == end_d.year and start_d.month == end_d.month:
        return f'{start_d.strftime("%a %-d")}–{end_d.strftime("%a %-d %B %Y")}'
    if start_d.year == end_d.year:
        return f'{start_d.strftime("%a %-d %B")}–{end_d.strftime("%a %-d %B %Y")}'
    return f'{start_d.strftime("%a %-d %B %Y")}–{end_d.strftime("%a %-d %B %Y")}'


def format_time_range(ev) -> str:
    start_dt, end_dt = ev["start_local"], ev["end_local"]

    def parts(dt):
        h = dt.strftime("%I").lstrip("0") or "0"
        return h, dt.strftime("%M"), dt.strftime("%p").lower()

    sh, sm, sampm = parts(start_dt)
    if end_dt:
        eh, em, eampm = parts(end_dt)
        if sampm == eampm:
            return f"{sh}:{sm}–{eh}:{em}{eampm}"
        return f"{sh}:{sm}{sampm}–{eh}:{em}{eampm}"
    return f"{sh}:{sm}{sampm}"


def build_show_details(ev):
    parts = []
    if ev["location"]:
        parts.append(esc(ev["location"]))
    if not ev["all_day"]:
        parts.append(format_time_range(ev))
    if not parts:
        return None
    return SEP.join(parts)


def render_show_item(ev) -> str:
    lines = ['          <li class="show-item">']
    lines.append(f'            <p class="show-date">{format_date_range(ev)}</p>')
    lines.append(f'            <p class="show-venue">{esc(ev["summary"]) or "TBA"}</p>')
    details = build_show_details(ev)
    if details:
        lines.append(f'            <p class="show-details">{details}</p>')
    if ev["context"]:
        context_html = esc(ev["context"]).replace("\n", "<br>")
        lines.append(f'            <p class="show-context">{context_html}</p>')
    if ev["ticket_url"]:
        url_attr = esc(ev["ticket_url"], quote=True)
        lines.append(
            f'            <a href="{url_attr}" class="btn show-ticket-link" '
            f'target="_blank" rel="noopener noreferrer">Tickets</a>'
        )
    lines.append("          </li>")
    return "\n".join(lines)


def render_highlight_item(ev) -> str:
    details_text = ev["context"] or ev["location"] or ""
    lines = ['          <li class="highlight-item">']
    lines.append(f'            <p class="highlight-date">{ev["start_date"].strftime("%b %Y")}</p>')
    lines.append(f'            <p class="highlight-venue">{esc(ev["summary"]) or "Untitled"}</p>')
    if details_text:
        lines.append(f'            <p class="highlight-details">{esc(details_text)}</p>')
    lines.append("          </li>")
    return "\n".join(lines)


def render_upcoming_block(events) -> str:
    if not events:
        return (
            '        <p class="live-no-shows">No shows announced. '
            'Join the <a href="#mailing">mailing list</a> to be first to know.</p>'
        )
    items = "\n\n".join(render_show_item(e) for e in events)
    return f'        <ul class="shows-list">\n\n{items}\n\n        </ul>'


def render_past_block(events) -> str:
    if not events:
        return '          <p class="live-no-shows">No past shows in the last 12 months.</p>'
    items = "\n\n".join(render_show_item(e) for e in events)
    return f'          <ul class="shows-list past-shows-list">\n\n{items}\n\n          </ul>'


def render_highlights_block(events) -> str:
    if not events:
        return '          <li class="highlight-item" style="opacity:.6">\n            <p class="highlight-details">No highlights yet.</p>\n          </li>'
    return "\n\n".join(render_highlight_item(e) for e in events)


# ---------------------------------------------------------------------------
# Splicing into index.html
# ---------------------------------------------------------------------------

def splice_marker(html_text: str, name: str, new_inner: str) -> str:
    pattern = re.compile(
        rf"(<!-- LIVE:{name}:START -->)(.*?)(<!-- LIVE:{name}:END -->)",
        re.DOTALL,
    )
    if not pattern.search(html_text):
        raise SystemExit(f"Could not find LIVE:{name} markers in the HTML file.")
    return pattern.sub(lambda m: f"{m.group(1)}\n{new_inner}\n{m.group(3)}", html_text, count=1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ical-url", default=os.environ.get("ICAL_URL"), help="Secret iCal feed URL (or set ICAL_URL env var)")
    parser.add_argument("--html-path", default="index.html", help="Path to index.html")
    parser.add_argument("--timezone", default="Australia/Sydney", help="IANA timezone for display/sorting")
    parser.add_argument("--past-months", type=int, default=12, help="How many months of past shows to keep")
    parser.add_argument("--window-past-months", type=int, default=15, help="How far back to expand recurring events (safety margin)")
    parser.add_argument("--window-future-months", type=int, default=24, help="How far forward to expand recurring events")
    parser.add_argument("--dry-run", action="store_true", help="Print the generated blocks instead of writing the file")
    args = parser.parse_args()

    if not args.ical_url:
        sys.exit("No iCal URL provided. Pass --ical-url or set the ICAL_URL environment variable.")

    tz = ZoneInfo(args.timezone)
    now = datetime.now(tz)

    window_start = now - relativedelta(months=args.window_past_months)
    window_end = now + relativedelta(months=args.window_future_months)

    ics_bytes = fetch_ics(args.ical_url)
    events = load_events(ics_bytes, tz, window_start, window_end)

    # --- Classification: DATE FIRST, TAG SECOND ---
    # An event's tag only matters once it's in the past. Upcoming events
    # always show as normal Live shows regardless of tagging, so Wally can
    # tag a gig the moment he creates it and never think about it again.
    past_cutoff = now - relativedelta(months=args.past_months)

    highlights = sorted(
        (e for e in events if e["is_highlight_tagged"] and e["sort_dt"] < now),
        key=lambda e: e["sort_dt"],
        reverse=True,
    )

    upcoming = sorted(
        (e for e in events if e["sort_dt"] >= now),
        key=lambda e: e["sort_dt"],
    )

    past = sorted(
        (e for e in events
         if not e["is_highlight_tagged"]
         and e["sort_dt"] < now
         and e["sort_dt"] >= past_cutoff),
        key=lambda e: e["sort_dt"],
        reverse=True,
    )

    print(f"Fetched {len(events)} event instance(s) in window "
          f"{window_start.date()}..{window_end.date()}")
    print(f"  upcoming: {len(upcoming)}  past(<= {args.past_months}mo): {len(past)}  highlights: {len(highlights)}")

    with open(args.html_path, "r", encoding="utf-8") as f:
        page = f.read()

    page = splice_marker(page, "UPCOMING", render_upcoming_block(upcoming))
    page = splice_marker(page, "PAST", render_past_block(past))
    page = splice_marker(page, "HIGHLIGHTS", render_highlights_block(highlights))

    if args.dry_run:
        print("--- DRY RUN: not writing file ---")
        sys.stdout.write(page)
        return

    with open(args.html_path, "w", encoding="utf-8") as f:
        f.write(page)


if __name__ == "__main__":
    main()
