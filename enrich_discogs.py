#!/usr/bin/env python3
"""
Enrich an existing tracklist JSON with Discogs genre/style data.

Usage:
  python3 enrich_discogs.py <tracklist.json>
  python3 enrich_discogs.py /root/tracklists/emiromer.json

Updates the genres field on each confirmed track in-place and
recalculates the top-level set genres from enriched track data.
"""

import json
import sys
import time
from collections import Counter

import requests

DISCOGS_USER_AGENT = "RonautRadio/1.0 +https://ronautradio.la"
DISCOGS_SEARCH_URL = "https://api.discogs.com/database/search"


def lookup_discogs_genres(artist: str, title: str) -> list:
    if not artist or not title or artist.lower() in ("unknown", ""):
        return []

    query = f"{artist} {title}"
    params = {"q": query, "type": "release", "per_page": 3}
    headers = {"User-Agent": DISCOGS_USER_AGENT}

    try:
        response = requests.get(DISCOGS_SEARCH_URL, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"  Discogs HTTP {response.status_code} for: {query}")
            return []
        data = response.json()
        results = data.get("results", [])
        if not results:
            return []

        top = results[0]
        genres = top.get("genre", [])
        styles = top.get("style", [])
        return list(dict.fromkeys(genres + styles))  # deduplicate, preserve order
    except Exception as e:
        print(f"  Discogs error for '{query}': {e}")
        return []


def enrich(json_path: str):
    with open(json_path) as f:
        data = json.load(f)

    tracklist = data.get("tracklist", [])
    if not tracklist:
        print("No confirmed tracks to enrich.")
        return

    print(f"Enriching {len(tracklist)} tracks via Discogs...\n")

    for track in tracklist:
        artist = track["artists"][0] if track.get("artists") else ""
        title = track.get("title", "")

        if not artist or not title:
            print(f"  Skipping (no artist/title): {artist} - {title}")
            continue

        original = track.get("genres", [])
        discogs = lookup_discogs_genres(artist, title)

        if discogs:
            track["genres"] = discogs
            print(f"  {artist} - {title}")
            print(f"    Before: {original}")
            print(f"    After:  {discogs}\n")
        else:
            print(f"  No Discogs result: {artist} - {title} (keeping: {original})\n")

        time.sleep(1.5)  # ~25 req/min unauthenticated

    # Recalculate top-level set genres from enriched tracks
    all_genres = []
    for track in tracklist:
        all_genres.extend(track.get("genres", []))
    genre_counts = Counter(all_genres)
    top_genres = [g for g, _ in genre_counts.most_common(5)]
    data["genres"] = top_genres

    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Set genres updated: {top_genres}")
    print(f"Saved to: {json_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 enrich_discogs.py <tracklist.json>")
        sys.exit(1)
    enrich(sys.argv[1])
