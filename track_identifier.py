#!/usr/bin/env python3
"""
Track Identifier for Ronaut Radio sets.

Extracts audio from MP4, chunks it, queries ACRCloud, and applies
confidence filtering to build a tracklist.
"""

import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

# ACRCloud credentials
ACR_HOST = "identify-us-west-2.acrcloud.com"
ACR_ACCESS_KEY = "93223fda5f0ce3be9e9458c4c515284c"
ACR_ACCESS_SECRET = "fovjEDkP7QPHpe9oYffhSLfx4LmRSREi3FvGjC2b"

# Recognition settings
CHUNK_DURATION = 20  # seconds per chunk
CHUNK_INTERVAL = 30  # seconds between chunk starts (can overlap or skip)
MIN_CONSECUTIVE = 2  # require N consecutive matches to confirm track
MAX_POPULARITY = 100000  # reject tracks with more Shazam plays than this
MIN_CONFIDENCE = 30  # minimum ACRCloud score (0-100) - lowered for rare vinyl


def sign_request(string_to_sign: str, access_secret: str) -> str:
    """Generate HMAC-SHA1 signature for ACRCloud."""
    return base64.b64encode(
        hmac.new(
            access_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")


def identify_chunk(audio_path: str) -> dict:
    """Send audio chunk to ACRCloud for identification."""
    http_method = "POST"
    http_uri = "/v1/identify"
    data_type = "audio"
    signature_version = "1"
    timestamp = str(int(time.time()))

    string_to_sign = (
        f"{http_method}\n{http_uri}\n{ACR_ACCESS_KEY}\n{data_type}\n{signature_version}\n{timestamp}"
    )
    signature = sign_request(string_to_sign, ACR_ACCESS_SECRET)

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    files = {"sample": ("chunk.mp3", audio_data, "audio/mpeg")}
    data = {
        "access_key": ACR_ACCESS_KEY,
        "sample_bytes": len(audio_data),
        "timestamp": timestamp,
        "signature": signature,
        "data_type": data_type,
        "signature_version": signature_version,
    }

    try:
        response = requests.post(
            f"https://{ACR_HOST}{http_uri}",
            files=files,
            data=data,
            timeout=30,
        )
        return response.json()
    except Exception as e:
        return {"status": {"code": -1, "msg": str(e)}}


def extract_chunk(mp4_path: str, start_time: int, duration: int, output_path: str) -> bool:
    """Extract audio chunk from MP4 using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_time),
        "-i", mp4_path,
        "-t", str(duration),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "44100",
        "-ac", "1",
        "-b:a", "128k",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def get_video_duration(mp4_path: str) -> int:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        mp4_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return int(float(result.stdout.strip()))
    except ValueError:
        return 0


def parse_acr_result(result: dict) -> dict | None:
    """Parse ACRCloud response into a simplified track dict."""
    if result.get("status", {}).get("code") != 0:
        return None

    metadata = result.get("metadata", {})
    music = metadata.get("music", [])

    if not music:
        return None

    track = music[0]  # Best match

    # Extract external metadata for popularity check
    external = track.get("external_metadata", {})
    spotify = external.get("spotify", {})
    deezer = external.get("deezer", {})

    # Use play_offset to understand match quality
    play_offset = track.get("play_offset_ms", 0)

    return {
        "acrid": track.get("acrid", ""),
        "title": track.get("title", "Unknown"),
        "artists": [a.get("name", "") for a in track.get("artists", [])],
        "album": track.get("album", {}).get("name", ""),
        "label": track.get("label", ""),
        "release_date": track.get("release_date", ""),
        "duration_ms": track.get("duration_ms", 0),
        "score": track.get("score", 0),
        "play_offset_ms": play_offset,
        "spotify_id": spotify.get("track", {}).get("id") if isinstance(spotify, dict) else None,
        "deezer_id": deezer.get("track", {}).get("id") if isinstance(deezer, dict) else None,
        "external_ids": track.get("external_ids", {}),
    }


def process_set(mp4_path: str, output_json: str = None) -> list:
    """
    Process an entire set, identify tracks with confidence filtering.
    """
    mp4_path = os.path.abspath(mp4_path)
    set_name = Path(mp4_path).stem

    if output_json is None:
        output_json = f"{set_name}_tracklist.json"

    print(f"Processing: {set_name}")

    # Get duration
    duration = get_video_duration(mp4_path)
    if duration == 0:
        print("Error: Could not determine video duration")
        return []

    print(f"Duration: {duration // 60}m {duration % 60}s")

    # Process chunks
    raw_matches = []
    chunk_times = list(range(0, duration - CHUNK_DURATION, CHUNK_INTERVAL))
    total_chunks = len(chunk_times)

    print(f"Processing {total_chunks} chunks...")

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, start_time in enumerate(chunk_times):
            chunk_path = os.path.join(tmpdir, f"chunk_{i:04d}.mp3")

            # Extract chunk
            if not extract_chunk(mp4_path, start_time, CHUNK_DURATION, chunk_path):
                print(f"  [{i+1}/{total_chunks}] {start_time//60}:{start_time%60:02d} - extraction failed")
                continue

            # Identify
            result = identify_chunk(chunk_path)
            track = parse_acr_result(result)

            if track:
                print(f"  [{i+1}/{total_chunks}] {start_time//60}:{start_time%60:02d} - {track['artists'][0] if track['artists'] else 'Unknown'} - {track['title']} (score: {track['score']})")
                raw_matches.append({
                    "chunk_index": i,
                    "start_time": start_time,
                    "track": track,
                })
            else:
                status = result.get("status", {})
                msg = status.get("msg", "No match")
                print(f"  [{i+1}/{total_chunks}] {start_time//60}:{start_time%60:02d} - {msg}")

            # Rate limiting - ACRCloud allows ~10 req/sec on free tier
            time.sleep(0.2)

    # Apply confidence filtering
    print("\nApplying confidence filters...")
    tracklist = apply_confidence_filter(raw_matches)

    # Save results
    output = {
        "set_name": set_name,
        "source_file": mp4_path,
        "duration_seconds": duration,
        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "settings": {
            "chunk_duration": CHUNK_DURATION,
            "chunk_interval": CHUNK_INTERVAL,
            "min_consecutive": MIN_CONSECUTIVE,
            "max_popularity": MAX_POPULARITY,
            "min_confidence": MIN_CONFIDENCE,
        },
        "raw_matches": raw_matches,
        "tracklist": tracklist,
    }

    with open(output_json, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to: {output_json}")
    print(f"Found {len(tracklist)} confirmed tracks")

    return tracklist


def apply_confidence_filter(raw_matches: list) -> list:
    """
    Filter raw ACRCloud matches using:
    1. Minimum confidence score
    2. Consecutive match requirement
    3. Popularity cap (TODO: requires external API)
    """
    if not raw_matches:
        return []

    # Filter by minimum confidence
    confident_matches = [
        m for m in raw_matches
        if m["track"]["score"] >= MIN_CONFIDENCE
    ]

    # Group consecutive matches by acrid
    tracklist = []
    current_track = None
    consecutive_count = 0
    first_seen_time = 0

    for match in confident_matches:
        acrid = match["track"]["acrid"]

        if current_track and current_track["acrid"] == acrid:
            consecutive_count += 1
        else:
            # Save previous track if it had enough consecutive matches
            if current_track and consecutive_count >= MIN_CONSECUTIVE:
                tracklist.append({
                    "start_time": first_seen_time,
                    "start_time_formatted": f"{first_seen_time//60}:{first_seen_time%60:02d}",
                    "acrid": current_track["acrid"],
                    "title": current_track["title"],
                    "artists": current_track["artists"],
                    "album": current_track["album"],
                    "label": current_track["label"],
                    "consecutive_matches": consecutive_count,
                    "confidence": "high" if consecutive_count >= 3 else "medium",
                })

            # Start tracking new track
            current_track = match["track"]
            consecutive_count = 1
            first_seen_time = match["start_time"]

    # Don't forget the last track
    if current_track and consecutive_count >= MIN_CONSECUTIVE:
        tracklist.append({
            "start_time": first_seen_time,
            "start_time_formatted": f"{first_seen_time//60}:{first_seen_time%60:02d}",
            "acrid": current_track["acrid"],
            "title": current_track["title"],
            "artists": current_track["artists"],
            "album": current_track["album"],
            "label": current_track["label"],
            "consecutive_matches": consecutive_count,
            "confidence": "high" if consecutive_count >= 3 else "medium",
        })

    return tracklist


def main():
    if len(sys.argv) < 2:
        print("Usage: python track_identifier.py <mp4_file> [output.json]")
        print("\nExample:")
        print("  python track_identifier.py /root/Andrea.mp4")
        print("  python track_identifier.py /root/Andrea.mp4 andrea_tracks.json")
        sys.exit(1)

    mp4_path = sys.argv[1]
    output_json = sys.argv[2] if len(sys.argv) > 2 else None

    if not os.path.exists(mp4_path):
        print(f"Error: File not found: {mp4_path}")
        sys.exit(1)

    tracklist = process_set(mp4_path, output_json)

    if tracklist:
        print("\n=== TRACKLIST ===")
        for i, track in enumerate(tracklist, 1):
            artists = ", ".join(track["artists"]) if track["artists"] else "Unknown"
            print(f"{i}. [{track['start_time_formatted']}] {artists} - {track['title']}")


if __name__ == "__main__":
    main()
