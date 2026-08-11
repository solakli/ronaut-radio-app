#!/usr/bin/env python3
"""
Add Discogs marketplace stats ("buy the wax") to enriched tracklist JSONs.

For each confirmed track that has a discogs_url (added by enrich_discogs.py),
extracts the release ID and queries /marketplace/stats/{release_id}.

Adds per-track:
  release_id: 12345
  wax: {"num_for_sale": 3, "lowest_price": 8.5, "currency": "USD", "checked": "2026-08-10"}

Prices go stale, so run nightly via cron:
  30 8 * * * python3 /root/ronaut-radio-app/wax_stats.py /root/tracklists/ >> /root/wax.log 2>&1

Optional: export DISCOGS_TOKEN=... for the authenticated rate limit (60 req/min vs 25).

Usage:
  python3 wax_stats.py <tracklist.json>    # single file
  python3 wax_stats.py /root/tracklists/   # all JSONs in dir
"""

import json
import os
import re
import sys
import time
from datetime import date

import requests

DISCOGS_USER_AGENT = "RonautRadio/1.0 +https://ronautradio.la"
DISCOGS_STATS_URL = "https://api.discogs.com/marketplace/stats/{}"
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN", "")
SLEEP_SECONDS = 1.5 if DISCOGS_TOKEN else 3


def extract_release_id(discogs_url: str):
    """Pull the numeric release ID out of a Discogs URL (handles old and new formats)."""
    m = re.search(r"/release/(\d+)", discogs_url or "")
    return int(m.group(1)) if m else None


def lookup_stats(release_id: int) -> dict:
    """Return marketplace stats for a release, or empty dict on failure."""
    headers = {"User-Agent": DISCOGS_USER_AGENT}
    if DISCOGS_TOKEN:
        headers["Authorization"] = f"Discogs token={DISCOGS_TOKEN}"
    params = {"curr_abbr": "USD"}

    try:
        response = requests.get(
            DISCOGS_STATS_URL.format(release_id), params=params, headers=headers, timeout=10
        )
        if response.status_code == 429:
            print("  Discogs rate limit hit — sleeping 60s")
            time.sleep(60)
            response = requests.get(
                DISCOGS_STATS_URL.format(release_id), params=params, headers=headers, timeout=10
            )
        if response.status_code != 200:
            print(f"  Discogs HTTP {response.status_code} for release {release_id}")
            return {}
        data = response.json()

        lowest = data.get("lowest_price") or {}
        wax = {
            "num_for_sale": data.get("num_for_sale", 0) or 0,
            "lowest_price": lowest.get("value"),
            "currency": lowest.get("currency", "USD"),
            "checked": date.today().isoformat(),
        }
        if data.get("blocked_from_sale"):
            wax["num_for_sale"] = 0
        return wax
    except Exception as e:
        print(f"  Discogs error for release {release_id}: {e}")
        return {}


def update(json_path: str):
    with open(json_path) as f:
        data = json.load(f)

    tracklist = data.get("tracklist", [])
    candidates = [
        t for t in tracklist
        if not t.get("needs_id") and t.get("discogs_url")
    ]
    if not candidates:
        print(f"  No tracks with discogs_url — skipping {os.path.basename(json_path)}")
        return

    print(f"\n=== {os.path.basename(json_path)} ({len(candidates)} tracks with Discogs links) ===")

    changed = 0
    for track in candidates:
        release_id = track.get("release_id") or extract_release_id(track.get("discogs_url", ""))
        if not release_id:
            print(f"  No release ID in URL: {track.get('discogs_url')}")
            continue

        wax = lookup_stats(release_id)
        artist = track["artists"][0] if track.get("artists") else "?"
        title = track.get("title", "?")

        if wax:
            track["release_id"] = release_id
            track["wax"] = wax
            changed += 1
            if wax["num_for_sale"] and wax["lowest_price"] is not None:
                print(f"  {artist} — {title}: {wax['num_for_sale']} for sale from ${wax['lowest_price']}")
            else:
                print(f"  {artist} — {title}: none for sale")
        else:
            # Keep any previous wax data — stale price beats no price
            print(f"  {artist} — {title}: lookup failed" +
                  (" (keeping previous stats)" if track.get("wax") else ""))

        time.sleep(SLEEP_SECONDS)

    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"  Saved. {changed}/{len(candidates)} tracks updated.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 wax_stats.py <tracklist.json|directory>")
        sys.exit(1)

    target = sys.argv[1]

    if os.path.isdir(target):
        files = sorted(
            f for f in os.listdir(target)
            if f.endswith(".json") and not f.endswith(".log")
        )
        print(f"Processing {len(files)} files in {target}")
        for fname in files:
            update(os.path.join(target, fname))
    else:
        update(target)
