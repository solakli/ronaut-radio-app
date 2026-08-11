#!/usr/bin/env python3
"""
Prepare Ronaut sets for SoundCloud upload.

For each given MP4 (or the default 320kbps list), writes into /root/soundcloud/:
  <name>.m4a  — audio extracted losslessly from the MP4 (-c:a copy, AAC)
  <name>.txt  — upload description: intro + timestamped tracklist + site plug
  <name>.jpg  — artwork copied from the set thumbnail

SoundCloud turns "H:MM:SS Artist — Title" description lines into clickable
seek links, so the enriched tracklists become interactive for free.

Usage:
  python3 sc_prep.py                       # default: the 320 kbps sets
  python3 sc_prep.py "Ronaut[009]-Talmadge-1.mp4" [more.mp4 ...]
"""

import json
import os
import re
import subprocess
import sys

OUT_DIR = "/root/soundcloud"
TRACKLIST_DIR = "/root/tracklists"
THUMB_DIR = "/var/www/html/sets/thumbs"
STAFF_PICKS = "/root/staff_picks.json"
BADGE_PATH = "/root/soundcloud/badge-overlay.png"  # transparent Ronaut badge
BADGE_SIZE = 180        # px, on a 1000x1000 cover
BADGE_MARGIN = 45
THUMB_SEEK = "00:05:00"  # same frame the site thumbnail uses

# 320 kbps sets (May 2026 audit) — lead with the best audio
DEFAULT_SETS = [
    "Ronaut[008]-Fede.mp4",
    "Ronaut[002]-Ayse.mp4",
    "Ronaut[010]-Thurs-Night.mp4",
    "Ronaut[006]-Emami.mp4",
    "Ronaut[014]-SSSunday(Andrea).mp4",
    "Ronaut[004]-Blend Brett.mp4",
    "Ronaut[009]-Talmadge-1.mp4",
    "Ronaut[007]-Emir.mp4",
]

JUNK = re.compile(r"^(none|n/a|unknown)$", re.IGNORECASE)


def normalize(name):
    base = os.path.basename(name or "")
    if base.lower().endswith(".mp4"):
        base = base[:-4]
    base = re.sub(r"^ronaut\[\d+\]\s*[-_ ]*", "", base.strip(), flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "", base.lower())


def load_tracklist(mp4):
    norm = normalize(mp4)
    stem = os.path.basename(mp4)[:-4]
    for cand in (f"{stem}_tracklist.json", f"{norm}_tracklist.json", f"{norm}.json"):
        path = os.path.join(TRACKLIST_DIR, cand)
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    return {}


def pick_info(mp4):
    try:
        with open(STAFF_PICKS) as f:
            data = json.load(f)
        sets = data["sets"] if isinstance(data, dict) else data
        norm = normalize(mp4)
        for s in sets:
            if normalize(s.get("filename", "")) == norm:
                return s
    except Exception as e:
        print(f"  staff_picks read failed: {e}")
    return {}


def build_description(mp4, info, tl_data):
    title = info.get("title") or os.path.basename(mp4)[:-4]
    desc = (info.get("description") or "").strip()
    lines = []
    intro = f"{title} — recorded live on Ronaut Radio, Los Angeles."
    if desc and desc.lower() != title.lower():
        intro += f" {desc}."
    lines.append(intro)
    lines.append("")

    def sc_ts(sec):
        """SoundCloud only links H:MM:SS / M:SS — not 76:00-style total minutes."""
        sec = int(sec or 0)
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    tracks = [t for t in tl_data.get("tracklist", []) if not t.get("needs_id")]
    tracks.sort(key=lambda t: t.get("start_time", 0))
    if tracks:
        lines.append("Tracklist:")
        for t in tracks:
            ts = sc_ts(t.get("start_time"))
            artist = t["artists"][0] if t.get("artists") else "Unknown"
            meta = [v for v in (t.get("label"), str(t.get("year") or "")) if v and not JUNK.match(v.strip())]
            suffix = f" ({', '.join(meta)})" if meta else ""
            lines.append(f"{ts} {artist} — {t.get('title', '')}{suffix}")
        lines.append("")

    lines.append("Vinyl only. Streaming 24/7 at https://ronautradio.la")
    return "\n".join(lines)


def find_thumb(mp4, info):
    cands = []
    if info.get("thumbnail"):
        cands.append(os.path.basename(info["thumbnail"]))
    cands.append(os.path.basename(mp4)[:-4] + ".jpg")
    for c in cands:
        path = os.path.join(THUMB_DIR, c)
        if os.path.exists(path):
            return path
    return None


def make_artwork(src_mp4, out_jpg):
    """Square 1000x1000 cover pulled fresh from the video (site thumbs are only 400px
    wide — too soft for SoundCloud), with the Ronaut badge in the bottom-left."""
    vf = ("crop='min(iw,ih)':'min(iw,ih)',scale=1000:1000")
    if os.path.exists(BADGE_PATH):
        filt = (f"[0:v]{vf}[bg];[1:v]scale={BADGE_SIZE}:{BADGE_SIZE}[b];"
                f"[bg][b]overlay={BADGE_MARGIN}:{1000 - BADGE_SIZE - BADGE_MARGIN}")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", THUMB_SEEK, "-i", src_mp4,
               "-i", BADGE_PATH, "-filter_complex", filt, "-frames:v", "1",
               "-q:v", "2", out_jpg]
    else:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", THUMB_SEEK, "-i", src_mp4,
               "-vf", vf, "-frames:v", "1", "-q:v", "2", out_jpg]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  artwork ffmpeg failed: {r.stderr[:160]}")
        return False
    return True


def prep(mp4):
    src = os.path.join("/root", mp4)
    if not os.path.exists(src):
        print(f"SKIP (no file): {mp4}")
        return
    stem = os.path.basename(mp4)[:-4]
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    audio_out = os.path.join(OUT_DIR, safe + ".m4a")
    print(f"=== {mp4}")

    if not os.path.exists(audio_out):
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vn", "-c:a", "copy", audio_out],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  ffmpeg FAILED: {r.stderr[:200]}")
            return
    size_mb = os.path.getsize(audio_out) / 1e6
    print(f"  audio: {os.path.basename(audio_out)} ({size_mb:.0f} MB)")

    info = pick_info(mp4)
    tl = load_tracklist(mp4)
    desc = build_description(mp4, info, tl)
    with open(os.path.join(OUT_DIR, safe + ".txt"), "w") as f:
        f.write(desc)
    n_tracks = len([t for t in tl.get("tracklist", []) if not t.get("needs_id")])
    print(f"  description: {n_tracks} tracks in tracklist")

    art_out = os.path.join(OUT_DIR, safe + ".jpg")
    if make_artwork(src, art_out):
        badged = " + badge" if os.path.exists(BADGE_PATH) else ""
        print(f"  artwork: 1000x1000 from video{badged}")
    else:
        thumb = find_thumb(mp4, info)
        if thumb:
            subprocess.run(["cp", thumb, art_out])
            print(f"  artwork: fell back to site thumbnail ({os.path.basename(thumb)})")
        else:
            print("  artwork: NOT FOUND")


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    targets = sys.argv[1:] or DEFAULT_SETS
    for mp4 in targets:
        prep(mp4)
    print(f"\nDone. Files in {OUT_DIR}/")
