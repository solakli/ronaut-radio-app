#!/usr/bin/env python3
"""
daily_gap_filler.py — Ronaut Radio

Picks a random set with unidentified gaps and probes up to PROBES_PER_RUN
timestamps using 2-shot Shazam validation (T and T+30s).
Both shots must return the same Shazam ID to confirm.

Budget: ~10 API calls/run (5 probes × 2 shots, abort shot 2 if shot 1 misses).
Already-probed timestamps (tracked in raw_matches) are skipped.

Cron: 0 3 * * * python3 /root/ronaut-radio-app/daily_gap_filler.py >> /root/tracklists/gap_filler.log 2>&1
"""

import base64
import glob
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime

import requests

# ── Config ──────────────────────────────────────────────────────────────────
TRACKLIST_DIR  = "/root/tracklists"
MP4_DIR        = "/root"
PROBES_PER_RUN = 5       # 2 API calls each = 10 max
PROBE_OFFSET   = 30      # seconds between shot 1 and shot 2
CHUNK_DURATION = 5       # seconds of audio per shot
MIN_GAP        = 120     # only target gaps longer than this (seconds)
PROBE_RADIUS   = 45      # skip if a raw_match exists within this many seconds

SHAZAM_API_URL  = "https://shazam.p.rapidapi.com/songs/v3/detect"
SHAZAM_API_KEY  = "fa8045a805mshe489b3f3302c27ep143a5ajsnd9b2ead05c0e"
SHAZAM_API_HOST = "shazam.p.rapidapi.com"
# ────────────────────────────────────────────────────────────────────────────


def fmt_time(s):
    return f"{int(s)//60}:{int(s)%60:02d}"


def normalize(name):
    name = re.sub(r"^ronaut\[\d+\]-?", "", name, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def find_mp4(source_file):
    """Resolve source_file to an existing path, falling back to normalized scan."""
    if source_file and os.path.isfile(source_file):
        return source_file
    # Try normalized match against /root/*.mp4
    base = os.path.basename(source_file or "")
    target = normalize(base.replace(".mp4", ""))
    for path in glob.glob(os.path.join(MP4_DIR, "*.mp4")):
        if normalize(os.path.basename(path).replace(".mp4", "")) == target:
            return path
    return None


def find_gaps(tracklist, duration):
    """Return list of (start, end) unidentified gaps > MIN_GAP seconds."""
    confirmed = sorted(
        [t for t in tracklist if not t.get("needs_id")],
        key=lambda t: t.get("start_time", 0)
    )
    gaps = []
    prev_end = 0
    for track in confirmed:
        t_start = track.get("start_time", 0)
        if t_start - prev_end > MIN_GAP:
            gaps.append((prev_end, t_start))
        prev_end = max(prev_end, track.get("end_time", t_start + 1))
    if duration and duration - prev_end > MIN_GAP:
        gaps.append((prev_end, duration))
    return gaps


def already_probed(raw_matches, timestamp):
    """True if a raw_match exists within PROBE_RADIUS seconds of timestamp."""
    for m in raw_matches:
        if abs(m.get("start_time", -9999) - timestamp) <= PROBE_RADIUS:
            return True
    return False


def extract_chunk(mp4_path, start_time):
    """Extract 5s raw PCM at start_time, return temp file path or None."""
    tmp = tempfile.NamedTemporaryFile(suffix=".raw", delete=False)
    tmp.close()
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(int(start_time)),
        "-i", mp4_path,
        "-t", str(CHUNK_DURATION),
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "44100", "-ac", "1",
        "-f", "s16le",
        tmp.name,
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not os.path.getsize(tmp.name):
        os.unlink(tmp.name)
        return None
    return tmp.name


def query_shazam(audio_path):
    """Send raw PCM to Shazam, return shazam_id string or None."""
    try:
        with open(audio_path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        resp = requests.post(
            SHAZAM_API_URL,
            headers={
                "Content-Type": "text/plain",
                "x-rapidapi-host": SHAZAM_API_HOST,
                "x-rapidapi-key": SHAZAM_API_KEY,
            },
            data=data,
            timeout=30,
        )
        if resp.status_code in (429, 403):
            msg = resp.json().get("message", f"HTTP {resp.status_code}")
            return None, {"api_error": msg, "status_code": resp.status_code}
        j = resp.json()
        matches = j.get("results", {}).get("matches", [])
        if not matches:
            return None, j
        return matches[0].get("id"), j
    except Exception as e:
        return None, {"error": str(e)}


def parse_track(shazam_id, raw_response):
    """Extract artist/title/label/year from Shazam v3 response."""
    resources = raw_response.get("resources", {})
    album_info = {}
    for a in resources.get("albums", {}).values():
        attrs = a.get("attributes", {})
        if attrs:
            album_info = attrs
            break
    artist_name = ""
    for a in resources.get("artists", {}).values():
        attrs = a.get("attributes", {})
        if attrs.get("name"):
            artist_name = attrs["name"]
            break
    genres = []
    for g in resources.get("genres", {}).values():
        name = g.get("attributes", {}).get("name", "")
        if name and name != "Music":
            genres.append(name)

    full_artist = album_info.get("artistName", artist_name)
    album_name  = album_info.get("name", "").replace(" - Single", "")
    release_date = album_info.get("releaseDate", "")

    return {
        "acrid":        f"shazam_{shazam_id}",
        "title":        album_name,
        "artists":      [full_artist] if full_artist else [],
        "album":        album_name,
        "label":        "",
        "release_date": release_date,
        "duration_ms":  0,
        "score":        100,
        "shazam_id":    shazam_id,
        "genres":       genres,
    }


def insert_track(tracklist, new_entry):
    """Insert a new confirmed track into the tracklist at the right position."""
    tracklist.append(new_entry)
    tracklist.sort(key=lambda t: t.get("start_time", 0))


def load_candidates():
    """Return list of (tracklist_path, data, mp4_path, gaps, total_gap_seconds)."""
    candidates = []
    for path in glob.glob(os.path.join(TRACKLIST_DIR, "*.json")):
        try:
            data = json.load(open(path))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        mp4 = find_mp4(data.get("source_file", ""))
        if not mp4:
            continue
        duration = data.get("duration_seconds", 0)
        gaps = find_gaps(data.get("tracklist", []), duration)
        if not gaps:
            continue
        total = sum(e - s for s, e in gaps)
        candidates.append((path, data, mp4, gaps, total))
    return candidates


def run():
    print(f"\n{'='*60}")
    print(f"gap_filler  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    candidates = load_candidates()
    if not candidates:
        print("No sets with gaps found.")
        return

    # Weighted random pick (sets with more unidentified time favoured)
    weights = [c[4] for c in candidates]
    chosen = random.choices(candidates, weights=weights, k=1)[0]
    path, data, mp4, gaps, total_gap = chosen

    set_name = data.get("set_name", os.path.basename(path))
    print(f"Set:   {set_name}")
    print(f"MP4:   {mp4}")
    print(f"Gaps:  {len(gaps)}  ({total_gap//60}m unidentified)")

    raw_matches = data.setdefault("raw_matches", [])
    tracklist   = data.setdefault("tracklist", [])

    # Build probe candidates: random timestamps in gaps, not already probed
    probe_candidates = []
    for (g_start, g_end) in gaps:
        usable = g_end - PROBE_OFFSET - CHUNK_DURATION - g_start
        if usable < CHUNK_DURATION:
            continue
        # Generate a few random candidate timestamps within this gap
        for _ in range(6):
            t = int(g_start + random.uniform(0, usable))
            if not already_probed(raw_matches, t):
                probe_candidates.append((t, g_start, g_end))

    random.shuffle(probe_candidates)
    probes = probe_candidates[:PROBES_PER_RUN]

    if not probes:
        print("All gap timestamps already probed.")
        return

    print(f"Probes: {len(probes)}\n")

    new_tracks = 0

    for (t, g_start, g_end) in probes:
        print(f"  Probing {fmt_time(t)}  (gap {fmt_time(g_start)}–{fmt_time(g_end)})")

        # Shot 1
        chunk1 = extract_chunk(mp4, t)
        if not chunk1:
            print(f"    ffmpeg failed at {t}s — skip")
            continue

        id1, resp1 = query_shazam(chunk1)
        os.unlink(chunk1)
        raw_matches.append({"start_time": t, "track": {"shazam_id": id1 or ""}})

        if resp1.get("api_error"):
            print(f"    API ERROR: {resp1['api_error']}")
            print("    ABORTING — upgrade Shazam plan at rapidapi.com")
            break

        if not id1:
            print(f"    Shot 1: no match")
            time.sleep(3)
            continue

        print(f"    Shot 1: {id1}")
        time.sleep(5)

        # Shot 2 (validation)
        t2 = t + PROBE_OFFSET
        chunk2 = extract_chunk(mp4, t2)
        if not chunk2:
            print(f"    ffmpeg failed at {t2}s — skip")
            continue

        id2, resp2 = query_shazam(chunk2)
        os.unlink(chunk2)
        raw_matches.append({"start_time": t2, "track": {"shazam_id": id2 or ""}})

        print(f"    Shot 2: {id2 or 'no match'}")

        if id1 == id2 and id1:
            track_data = parse_track(id1, resp1)
            artist = ", ".join(track_data["artists"])
            title  = track_data["title"]
            year   = track_data["release_date"][:4] if track_data["release_date"] else ""

            entry = {
                "start_time":           t,
                "end_time":             g_end,
                "start_time_formatted": fmt_time(t),
                "end_time_formatted":   fmt_time(g_end),
                "duration_seconds":     g_end - t,
                "acrid":                track_data["acrid"],
                "title":                title,
                "artists":              track_data["artists"],
                "album":                track_data["album"],
                "label":                "",
                "year":                 year,
                "genres":               track_data["genres"],
                "consecutive_matches":  2,
                "confidence":           "gap_filler",
            }
            insert_track(tracklist, entry)
            new_tracks += 1
            print(f"    ✓ MATCH: {artist} — {title} ({year})")
        else:
            print(f"    IDs differ — not confirmed")

        time.sleep(5)

    # Save back
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nDone. {new_tracks} new track(s) added. Saved to {os.path.basename(path)}")


if __name__ == "__main__":
    run()
