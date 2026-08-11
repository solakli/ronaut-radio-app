#!/usr/bin/env python3
"""
Sync every Discogs-identified track from the Ronaut tracklists into the
ronaut Discogs wantlist, via Wax Digger's API (localhost:5052).

Waxy's /api/wantlist/add pushes to the real Discogs wantlist through the
station's OAuth token, mirrors it in Waxy's DB, and queues meta-sync —
so Waxy's daily store rescan + alerts then cover everything played on air.

A ledger (/root/wantlist_synced.json) records every release ID ever pushed.
Releases in the ledger are never pushed again, so records pruned from the
wantlist on discogs.com STAY pruned.

Cron (nightly, picks up newly identified sets):
  45 8 * * * python3 /root/ronaut-radio-app/wantlist_sync.py >> /root/wantlist.log 2>&1

Usage:
  python3 wantlist_sync.py [--dry-run] [tracklist_dir]
"""

import json
import os
import re
import sys
import time
from datetime import datetime

import requests

WAXY_API = "http://localhost:5052/api/wantlist/add"
WAXY_USERNAME = "ronaut"
LEDGER_PATH = "/root/wantlist_synced.json"
DEFAULT_TRACKLIST_DIR = "/root/tracklists"
SLEEP_SECONDS = 2  # each add hits Discogs through OAuth — stay polite


def load_ledger() -> dict:
    if os.path.exists(LEDGER_PATH):
        with open(LEDGER_PATH) as f:
            return json.load(f)
    return {"synced": {}}


def save_ledger(ledger: dict):
    with open(LEDGER_PATH, "w") as f:
        json.dump(ledger, f, indent=2)


def extract_release_id(track: dict):
    if track.get("release_id"):
        return int(track["release_id"])
    m = re.search(r"/release/(\d+)", track.get("discogs_url", ""))
    return int(m.group(1)) if m else None


def collect_tracks(tracklist_dir: str) -> dict:
    """Map release_id -> track metadata across all tracklist JSONs (first seen wins)."""
    releases = {}
    for fname in sorted(os.listdir(tracklist_dir)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(tracklist_dir, fname)) as f:
                data = json.load(f)
        except Exception as e:
            print(f"  Skipping {fname}: {e}")
            continue
        for track in data.get("tracklist", []):
            if track.get("needs_id"):
                continue
            release_id = extract_release_id(track)
            if not release_id or release_id in releases:
                continue
            releases[release_id] = {
                "discogsId": release_id,
                "artist": track["artists"][0] if track.get("artists") else "",
                "title": track.get("title", ""),
                "year": track.get("year") or None,
                "label": track.get("label", ""),
                "catno": track.get("catno", ""),
                "genres": ", ".join(track.get("genres", [])[:5]),
            }
    return releases


def main():
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tracklist_dir = args[0] if args else DEFAULT_TRACKLIST_DIR

    releases = collect_tracks(tracklist_dir)
    ledger = load_ledger()
    pending = {rid: meta for rid, meta in releases.items()
               if str(rid) not in ledger["synced"]}

    print(f"[{datetime.now().isoformat(timespec='seconds')}] "
          f"{len(releases)} identified releases, {len(pending)} not yet synced")

    if dry_run:
        for rid, meta in sorted(pending.items()):
            print(f"  would add {rid}: {meta['artist']} — {meta['title']}")
        return

    added = failed = 0
    for rid, meta in sorted(pending.items()):
        try:
            resp = requests.post(WAXY_API, json=dict(meta, username=WAXY_USERNAME), timeout=30)
            if resp.status_code == 200:
                ledger["synced"][str(rid)] = {
                    "added": datetime.now().isoformat(timespec="seconds"),
                    "artist": meta["artist"],
                    "title": meta["title"],
                }
                save_ledger(ledger)  # save as we go — a crash mid-run loses nothing
                added += 1
                print(f"  added {rid}: {meta['artist']} — {meta['title']}")
            else:
                failed += 1
                print(f"  FAILED {rid} ({resp.status_code}): {meta['artist']} — {meta['title']} "
                      f"— {resp.text[:120]}")
        except Exception as e:
            failed += 1
            print(f"  FAILED {rid}: {e}")
        time.sleep(SLEEP_SECONDS)

    print(f"Done. {added} added, {failed} failed, "
          f"{len(ledger['synced'])} total in ledger.")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
