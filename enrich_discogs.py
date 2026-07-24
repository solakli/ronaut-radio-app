#!/usr/bin/env python3
"""
Enrich an existing tracklist JSON with Discogs metadata.

Adds per-track: year, label, catno, genres/styles, discogs_url.
Also falls back to Shazam release_date for year if Discogs has none.
Recalculates top-level set genres from enriched track data.

Usage:
  python3 enrich_discogs.py <tracklist.json>           # single file
  python3 enrich_discogs.py /root/tracklists/           # all JSONs in dir
"""

import json
import os
import sys
import time
from collections import Counter

import requests

DISCOGS_USER_AGENT = "RonautRadio/1.0 +https://ronautradio.la"
DISCOGS_SEARCH_URL = "https://api.discogs.com/database/search"


def lookup_discogs(artist: str, title: str) -> dict:
    """Return dict with year, label, catno, genres, discogs_url from Discogs, or empty dict."""
    if not artist or not title or artist.lower() in ("unknown", ""):
        return {}

    query = f"{artist} {title}"
    params = {"q": query, "type": "release", "per_page": 3}
    headers = {"User-Agent": DISCOGS_USER_AGENT}

    try:
        response = requests.get(DISCOGS_SEARCH_URL, params=params, headers=headers, timeout=10)
        if response.status_code == 429:
            print("  Discogs rate limit hit — sleeping 60s")
            time.sleep(60)
            response = requests.get(DISCOGS_SEARCH_URL, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"  Discogs HTTP {response.status_code} for: {query}")
            return {}
        data = response.json()
        results = data.get("results", [])
        if not results:
            return {}

        top = results[0]
        genres = top.get("genre", [])
        styles = top.get("style", [])
        labels = top.get("label", [])
        uri = top.get("uri", "")
        discogs_url = f"https://www.discogs.com{uri}" if uri else ""

        return {
            "year": str(top.get("year", "")).strip() or "",
            "label": labels[0] if labels else "",
            "catno": top.get("catno", "").strip(),
            "genres": list(dict.fromkeys(genres + styles)),
            "discogs_url": discogs_url,
        }
    except Exception as e:
        print(f"  Discogs error for '{query}': {e}")
        return {}


def _shazam_year_for_track(data: dict, track: dict) -> str:
    """Pull release_date out of raw_matches for this track's acrid."""
    acrid = track.get("acrid", "")
    if not acrid:
        return ""
    for raw in data.get("raw_matches", []):
        t = raw.get("track", {})
        if t.get("acrid") == acrid:
            rd = t.get("release_date", "")
            # Shazam release_date is often "YYYY" or "YYYY-MM-DD"
            return rd[:4] if rd else ""
    return ""


def enrich(json_path: str):
    with open(json_path) as f:
        data = json.load(f)

    tracklist = data.get("tracklist", [])
    confirmed = [t for t in tracklist if not t.get("needs_id")]
    if not confirmed:
        print(f"  No confirmed tracks — skipping {os.path.basename(json_path)}")
        return

    print(f"\n=== {os.path.basename(json_path)} ({len(confirmed)} tracks) ===")

    changed = 0
    for track in confirmed:
        artist = track["artists"][0] if track.get("artists") else ""
        title = track.get("title", "")

        if not artist or not title:
            print(f"  Skip (no artist/title): {artist} — {title}")
            continue

        result = lookup_discogs(artist, title)

        if result:
            # Year: prefer Discogs, fall back to Shazam release_date
            year = result.get("year") or _shazam_year_for_track(data, track)
            label = result.get("label", "")
            catno = result.get("catno", "")
            genres = result.get("genres", [])
            discogs_url = result.get("discogs_url", "")

            track["year"] = year
            track["label"] = label
            track["catno"] = catno
            if genres:
                track["genres"] = genres
            if discogs_url:
                track["discogs_url"] = discogs_url

            meta = " · ".join(filter(None, [label, catno, year]))
            print(f"  {artist} — {title}")
            print(f"    {meta or '(no meta)'} | genres: {genres[:3]}")
            changed += 1
        else:
            # Still try to pull year from Shazam raw data
            year = _shazam_year_for_track(data, track)
            if year and not track.get("year"):
                track["year"] = year
                changed += 1
            print(f"  No Discogs result: {artist} — {title}" + (f" (year from Shazam: {year})" if year else ""))

        time.sleep(3)  # stay under ~20 req/min unauthenticated

    # Recalculate top-level set genres from enriched tracks
    all_genres = []
    for track in confirmed:
        all_genres.extend(track.get("genres", []))
    genre_counts = Counter(all_genres)
    top_genres = [g for g, _ in genre_counts.most_common(5)]
    data["genres"] = top_genres

    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"  Saved. {changed}/{len(confirmed)} tracks enriched. Set genres: {top_genres}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 enrich_discogs.py <tracklist.json|directory>")
        sys.exit(1)

    target = sys.argv[1]

    if os.path.isdir(target):
        files = sorted(
            f for f in os.listdir(target)
            if f.endswith(".json") and not f.endswith(".log")
        )
        print(f"Processing {len(files)} files in {target}")
        for fname in files:
            enrich(os.path.join(target, fname))
    else:
        enrich(target)
