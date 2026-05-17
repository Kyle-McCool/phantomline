"""Weekly GSC report — striking-distance opportunities for Phantomline.

What it does
------------
Pulls the last 28 days of GSC performance data, then surfaces:
1. Pages ranking at positions 5-20 with >=3 impressions (the "striking
   distance" pages — one signal-boost away from page 1).
2. Queries we appear for at positions 5-20 with >=5 impressions but 0
   clicks (likely a title-tag mismatch we can fix).
3. Pages that lost >10 positions vs the prior 28-day window (regression
   alerts — fix before they fall off page 1 entirely).
4. New queries that appeared in the last 7 days but not the prior 21
   (early signals of content Google is starting to surface).

Output
------
Prints a tight markdown report to stdout. Pipe it to a file, paste into
Slack/Notion, or run weekly via cron / a GitHub Action.

Setup (one-time)
----------------
1. Create a Google Cloud project at https://console.cloud.google.com
2. Enable the Search Console API
3. Create a service account, download the JSON key
4. In Google Search Console → Settings → Users and permissions → Add the
   service-account email as a "Restricted" user
5. Save the JSON key to ~/.gsc/phantomline.json
6. pip install google-api-python-client google-auth
7. Run: python scripts/gsc_weekly_report.py

Cron example (every Monday at 9 AM)
-----------------------------------
0 9 * * 1 cd /path/to/phantomline && python scripts/gsc_weekly_report.py \
    > weekly_gsc_$(date +\\%Y\\%m\\%d).md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path


SITE_URL = "https://phantomline.xyz/"
DEFAULT_CREDS = Path.home() / ".gsc" / "phantomline.json"

STRIKING_POS_MIN = 5
STRIKING_POS_MAX = 20
STRIKING_MIN_IMPRESSIONS = 3
ZERO_CLICK_MIN_IMPRESSIONS = 5
REGRESSION_THRESHOLD = 10


def _build_service(creds_path: Path):
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        print(
            "Missing deps. Install with:\n  "
            "pip install google-api-python-client google-auth",
            file=sys.stderr,
        )
        sys.exit(2)

    scopes = ["https://www.googleapis.com/auth/webmasters.readonly"]
    creds = service_account.Credentials.from_service_account_file(
        str(creds_path), scopes=scopes
    )
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def _query(svc, start: date, end: date, dimensions: list[str], row_limit: int = 5000):
    """Run a Search Analytics query for the date range + dimensions."""
    request = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": dimensions,
        "rowLimit": row_limit,
    }
    response = svc.searchanalytics().query(siteUrl=SITE_URL, body=request).execute()
    return response.get("rows", [])


def _to_dict(rows: list[dict], key_name: str) -> dict[str, dict]:
    """Turn GSC rows (with `keys: [...]`) into a {key: metrics} dict."""
    out = {}
    for r in rows:
        k = r["keys"][0]
        out[k] = {
            "clicks": r.get("clicks", 0),
            "impressions": r.get("impressions", 0),
            "ctr": r.get("ctr", 0),
            "position": r.get("position", 0),
        }
    return out


def _striking_distance(rows: list[dict]) -> list[dict]:
    """Pages or queries at position 5-20 with enough impressions to act on."""
    keep = []
    for r in rows:
        pos = r.get("position", 0)
        imps = r.get("impressions", 0)
        if STRIKING_POS_MIN <= pos <= STRIKING_POS_MAX and imps >= STRIKING_MIN_IMPRESSIONS:
            keep.append(r)
    return sorted(keep, key=lambda r: (r["position"], -r["impressions"]))


def _zero_click_queries(rows: list[dict]) -> list[dict]:
    """Queries with impressions but no clicks at a salvageable position."""
    keep = []
    for r in rows:
        if (
            r.get("clicks", 0) == 0
            and r.get("impressions", 0) >= ZERO_CLICK_MIN_IMPRESSIONS
            and r.get("position", 0) <= STRIKING_POS_MAX
        ):
            keep.append(r)
    return sorted(keep, key=lambda r: -r["impressions"])


def _regressions(current: dict, prior: dict) -> list[tuple[str, float, float]]:
    """Pages that dropped >= REGRESSION_THRESHOLD positions since last window."""
    drops = []
    for url, cur in current.items():
        if url not in prior:
            continue
        prev_pos = prior[url]["position"]
        cur_pos = cur["position"]
        delta = cur_pos - prev_pos  # positive = ranked worse
        if delta >= REGRESSION_THRESHOLD and cur["impressions"] >= 3:
            drops.append((url, prev_pos, cur_pos))
    return sorted(drops, key=lambda x: -(x[2] - x[1]))


def _new_queries(recent: set[str], prior: set[str]) -> list[str]:
    return sorted(recent - prior)


def _format_pos(p: float) -> str:
    return f"{p:.1f}" if isinstance(p, float) else str(p)


def _format_pct(p: float) -> str:
    return f"{p*100:.1f}%"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--creds",
        type=Path,
        default=DEFAULT_CREDS,
        help=f"Path to GSC service-account JSON (default: {DEFAULT_CREDS})",
    )
    parser.add_argument(
        "--site",
        default=SITE_URL,
        help="GSC property URL (must match exactly, including trailing /)",
    )
    args = parser.parse_args()

    if not args.creds.exists():
        print(f"Credentials not found: {args.creds}", file=sys.stderr)
        print(
            "\nSetup:\n"
            "  1. Create a Google Cloud service account\n"
            "  2. Enable Search Console API\n"
            "  3. Add service-account email to GSC as Restricted user\n"
            "  4. Save the JSON key to ~/.gsc/phantomline.json\n",
            file=sys.stderr,
        )
        sys.exit(1)

    global SITE_URL
    SITE_URL = args.site
    svc = _build_service(args.creds)

    today = date.today()
    # GSC data lags ~2 days, so anchor at today - 2.
    cur_end = today - timedelta(days=2)
    cur_start = cur_end - timedelta(days=27)
    prior_end = cur_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=27)
    recent_start = cur_end - timedelta(days=6)
    older_start = cur_start
    older_end = recent_start - timedelta(days=1)

    print(f"# GSC weekly report — {today.isoformat()}")
    print(f"Site: `{SITE_URL}`  ·  Window: {cur_start} → {cur_end} (28d)")
    print()

    # --- Section 1: Striking-distance pages ---
    page_rows = _query(svc, cur_start, cur_end, ["page"])
    striking_pages = _striking_distance(page_rows)
    print("## Striking-distance pages (positions 5-20, ready to push)")
    if not striking_pages:
        print("_None this week. Either nothing is in striking distance yet, "
              "or your top pages already broke onto page 1. Check section 4 "
              "for new query signals._")
    else:
        print()
        print("| Page | Position | Impressions | Clicks | CTR |")
        print("|------|---------:|------------:|-------:|----:|")
        for r in striking_pages[:15]:
            page = r["keys"][0].replace(SITE_URL.rstrip("/"), "")
            print(
                f"| `{page}` | {_format_pos(r['position'])} | "
                f"{int(r['impressions'])} | {int(r['clicks'])} | "
                f"{_format_pct(r['ctr'])} |"
            )
    print()

    # --- Section 2: Zero-click queries (CTR salvage candidates) ---
    query_rows = _query(svc, cur_start, cur_end, ["query"])
    zero_click = _zero_click_queries(query_rows)
    print("## Zero-click queries at salvageable positions")
    if not zero_click:
        print("_Either everything that ranks well is clicking, or you have "
              "very few queries ranking in top 20 yet._")
    else:
        print()
        print("| Query | Position | Impressions |")
        print("|-------|---------:|------------:|")
        for r in zero_click[:15]:
            q = r["keys"][0]
            print(f"| `{q}` | {_format_pos(r['position'])} | "
                  f"{int(r['impressions'])} |")
    print()

    # --- Section 3: Regressions ---
    prior_page_rows = _query(svc, prior_start, prior_end, ["page"])
    cur_pages = _to_dict(page_rows, "page")
    prior_pages = _to_dict(prior_page_rows, "page")
    regressed = _regressions(cur_pages, prior_pages)
    print(f"## Regressed pages (lost >={REGRESSION_THRESHOLD} positions)")
    if not regressed:
        print("_None this week. Rankings are stable or trending up._")
    else:
        print()
        print("| Page | Was | Now | Delta |")
        print("|------|----:|----:|------:|")
        for url, was, now in regressed[:10]:
            page = url.replace(SITE_URL.rstrip("/"), "")
            print(f"| `{page}` | {_format_pos(was)} | "
                  f"{_format_pos(now)} | +{now-was:.1f} |")
    print()

    # --- Section 4: New queries ---
    recent_rows = _query(svc, recent_start, cur_end, ["query"])
    older_rows = _query(svc, older_start, older_end, ["query"])
    recent_set = {r["keys"][0] for r in recent_rows if r.get("impressions", 0) >= 2}
    older_set = {r["keys"][0] for r in older_rows}
    new_queries = _new_queries(recent_set, older_set)
    print("## New queries (last 7 days, not previously ranking)")
    if not new_queries:
        print("_No new queries surfaced this week. Try publishing fresh content "
              "or rebuilding internal links to underused pages._")
    else:
        for q in new_queries[:20]:
            print(f"- `{q}`")
    print()

    # --- Action summary ---
    print("## What to do this week")
    actions = []
    if striking_pages:
        top = striking_pages[0]
        page = top["keys"][0].replace(SITE_URL.rstrip("/"), "")
        actions.append(
            f"**Push `{page}` onto page 1** — currently at "
            f"position {_format_pos(top['position'])}. Add 2-3 internal "
            f"links from higher-authority pages and request reindex in GSC."
        )
    if zero_click:
        top = zero_click[0]
        actions.append(
            f"**Rewrite the title for whatever page ranks for "
            f"`{top['keys'][0]}`** — {int(top['impressions'])} impressions "
            f"with zero clicks at position {_format_pos(top['position'])} "
            f"means the SERP snippet isn't selling."
        )
    if regressed:
        url, was, now = regressed[0]
        page = url.replace(SITE_URL.rstrip("/"), "")
        actions.append(
            f"**Investigate `{page}`** — dropped from "
            f"position {_format_pos(was)} to {_format_pos(now)}. "
            f"Could be a Google algorithm change, a competitor outranking "
            f"you, or content drift."
        )
    if not actions:
        actions.append(
            "No clear high-leverage moves this week. Focus on backlinks "
            "and publishing new content."
        )
    for i, action in enumerate(actions, 1):
        print(f"{i}. {action}")


if __name__ == "__main__":
    main()
