#!/usr/bin/env python3
"""
wantlist_sync.py — push every Discogs release ever ID'd on Ronaut Radio
into the Waxy user `ronaut`'s real Discogs wantlist.

Sweeps /root/tracklists/*.json for tracks with a discogs_url, dedupes
release IDs, and POSTs each NEW one to Waxy's /api/wantlist/add (which
writes to discogs.com via the stored OAuth token, mirrors locally, and
queues metadata sync).

Add-only with a ledger: /root/ronaut-radio-app/wantlist_synced.json
records every release ID successfully added. Ledgered IDs are never sent
again, so manual prunes on discogs.com stick. Failures are not ledgered
and retry on the next run.

Runs nightly from cron, chained after wax_stats.py. Safe to re-run.
"""

import glob
import json
import os
import re
import sys
import time

import requests

TRACKLIST_DIR = "/root/tracklists"
LEDGER_PATH = "/root/ronaut-radio-app/wantlist_synced.json"
WAXY_ADD_URL = "http://localhost:5052/api/wantlist/add"
WAXY_USERNAME = "ronaut"
SLEEP_BETWEEN_ADDS = 1.5  # ~40/min, under Discogs' 60/min OAuth ceiling

RELEASE_RE = re.compile(r"/release/(\d+)")


def load_ledger():
    if os.path.exists(LEDGER_PATH):
        with open(LEDGER_PATH) as f:
            return json.load(f)
    return {"synced": {}}


def save_ledger(ledger):
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ledger, f, indent=1, sort_keys=True)
    os.replace(tmp, LEDGER_PATH)


def collect_releases():
    """Return {release_id: track_meta} for every identified track, first-seen wins."""
    releases = {}
    files = sorted(glob.glob(os.path.join(TRACKLIST_DIR, "*.json")))
    for path in files:
        try:
            with open(path) as f:
                data = json.load(f)
        except (ValueError, OSError) as e:
            print(f"  ! could not read {os.path.basename(path)}: {e}")
            continue
        for t in data.get("tracklist", []):
            if t.get("needs_id"):
                continue
            m = RELEASE_RE.search(t.get("discogs_url") or "")
            if not m:
                continue
            rid = int(m.group(1))
            if rid not in releases:
                releases[rid] = {
                    "artist": ", ".join(t.get("artists") or []),
                    "title": t.get("album") or t.get("title") or "",
                    "year": t.get("year") or None,
                    "label": t.get("label") or "",
                    "catno": t.get("catno") or "",
                    "genres": ", ".join(t.get("genres") or []),
                    "set": data.get("set_name") or os.path.basename(path),
                }
    return releases


def main():
    releases = collect_releases()
    ledger = load_ledger()
    synced = ledger.setdefault("synced", {})

    new = {rid: meta for rid, meta in releases.items() if str(rid) not in synced}
    print(f"tracklists: {len(releases)} unique releases; "
          f"{len(releases) - len(new)} already synced; {len(new)} to add")

    added, failed = 0, []
    for rid, meta in sorted(new.items()):
        try:
            r = requests.post(WAXY_ADD_URL, json={
                "username": WAXY_USERNAME,
                "discogsId": rid,
                "artist": meta["artist"],
                "title": meta["title"],
                "year": meta["year"],
                "label": meta["label"],
                "catno": meta["catno"],
                "genres": meta["genres"],
            }, timeout=30)
            if r.status_code == 200 and r.json().get("ok"):
                synced[str(rid)] = time.strftime("%Y-%m-%d")
                save_ledger(ledger)  # persist per-add so a crash loses nothing
                added += 1
                print(f"  + {rid}  {meta['artist']} — {meta['title']}  [{meta['set']}]")
            else:
                failed.append((rid, f"HTTP {r.status_code}: {r.text[:120]}"))
                print(f"  ! {rid}  {meta['artist']} — {meta['title']}: "
                      f"HTTP {r.status_code} {r.text[:120]}")
        except requests.RequestException as e:
            failed.append((rid, str(e)))
            print(f"  ! {rid}  {meta['artist']} — {meta['title']}: {e}")
        time.sleep(SLEEP_BETWEEN_ADDS)

    print(f"\nsummary: added {added}, already-synced {len(releases) - len(new)}, "
          f"failed {len(failed)}")
    if failed:
        print("failures (will retry next run):")
        for rid, err in failed:
            print(f"  {rid}: {err}")
    return 1 if failed and not added else 0


if __name__ == "__main__":
    sys.exit(main())
