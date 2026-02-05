#!/usr/bin/env python3
"""
Track Identifier for Ronaut Radio sets.

Extracts audio from MP4, chunks it, queries ACRCloud and Shazam, and applies
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

# Shazam (RapidAPI) credentials
SHAZAM_API_URL = "https://shazam.p.rapidapi.com/songs/v3/detect"
SHAZAM_API_KEY = "fa8045a805mshe489b3f3302c27ep143a5ajsnd9b2ead05c0e"
SHAZAM_API_HOST = "shazam.p.rapidapi.com"

# Recognition settings
CHUNK_DURATION_ACR = 20  # seconds per chunk for ACRCloud
CHUNK_DURATION_SHAZAM = 5  # seconds per chunk for Shazam (API has size limit)
CHUNK_INTERVAL = 30  # seconds between chunk starts (can overlap or skip)
MIN_CONSECUTIVE = 2  # require N consecutive matches to confirm track
MAX_POPULARITY = 100000  # reject tracks with more Shazam plays than this
MIN_CONFIDENCE = 30  # minimum ACRCloud score (0-100) - lowered for rare vinyl
USE_SHAZAM = True  # Use Shazam as primary (True) or ACRCloud (False)


def sign_request(string_to_sign: str, access_secret: str) -> str:
    """Generate HMAC-SHA1 signature for ACRCloud."""
    return base64.b64encode(
        hmac.new(
            access_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("utf-8")


def extract_chunk_raw(mp4_path: str, start_time: int, duration: int, output_path: str) -> bool:
    """Extract audio chunk for Shazam as base64-friendly format."""
    # Use MP3 for Shazam - 5 seconds at 128kbps = ~80KB
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


def identify_chunk_shazam(audio_path: str) -> dict:
    """Send audio chunk to Shazam via RapidAPI for identification."""
    try:
        with open(audio_path, "rb") as f:
            audio_data = f.read()

        # Shazam expects base64-encoded raw audio
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")

        headers = {
            "Content-Type": "text/plain",
            "x-rapidapi-host": SHAZAM_API_HOST,
            "x-rapidapi-key": SHAZAM_API_KEY,
        }

        response = requests.post(
            SHAZAM_API_URL,
            headers=headers,
            data=audio_b64,
            timeout=30,
        )
        return response.json()
    except Exception as e:
        return {"error": str(e)}


def parse_shazam_result(result: dict) -> dict | None:
    """Parse Shazam response into a simplified track dict."""
    if "error" in result:
        return None

    track = result.get("track")
    if not track:
        return None

    # Extract artist names
    artists = []
    if track.get("subtitle"):
        artists = [track["subtitle"]]

    # Get Shazam key as unique ID
    shazam_key = track.get("key", "")

    return {
        "acrid": f"shazam_{shazam_key}",  # Use shazam_ prefix for Shazam IDs
        "title": track.get("title", "Unknown"),
        "artists": artists,
        "album": track.get("sections", [{}])[0].get("metadata", [{}])[0].get("text", "") if track.get("sections") else "",
        "label": "",
        "release_date": "",
        "duration_ms": 0,
        "score": 100,  # Shazam doesn't give confidence, assume high if matched
        "play_offset_ms": 0,
        "spotify_id": None,
        "deezer_id": None,
        "external_ids": {},
        "shazam_key": shazam_key,
        "shazam_url": track.get("url", ""),
    }


def identify_chunk_acr(audio_path: str) -> dict:
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
    chunk_dur = CHUNK_DURATION_SHAZAM if USE_SHAZAM else CHUNK_DURATION_ACR
    chunk_times = list(range(0, duration - chunk_dur, CHUNK_INTERVAL))
    total_chunks = len(chunk_times)

    api_name = "Shazam" if USE_SHAZAM else "ACRCloud"
    print(f"Processing {total_chunks} chunks using {api_name}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, start_time in enumerate(chunk_times):
            # Use different formats for each API
            if USE_SHAZAM:
                chunk_path = os.path.join(tmpdir, f"chunk_{i:04d}.mp3")
                extract_ok = extract_chunk_raw(mp4_path, start_time, chunk_dur, chunk_path)
            else:
                chunk_path = os.path.join(tmpdir, f"chunk_{i:04d}.mp3")
                extract_ok = extract_chunk(mp4_path, start_time, chunk_dur, chunk_path)

            if not extract_ok:
                print(f"  [{i+1}/{total_chunks}] {start_time//60}:{start_time%60:02d} - extraction failed")
                continue

            # Identify using selected API
            if USE_SHAZAM:
                result = identify_chunk_shazam(chunk_path)
                track = parse_shazam_result(result)
            else:
                result = identify_chunk_acr(chunk_path)
                track = parse_acr_result(result)

            if track:
                print(f"  [{i+1}/{total_chunks}] {start_time//60}:{start_time%60:02d} - {track['artists'][0] if track['artists'] else 'Unknown'} - {track['title']} (score: {track['score']})")
                raw_matches.append({
                    "chunk_index": i,
                    "start_time": start_time,
                    "track": track,
                })
            else:
                if USE_SHAZAM:
                    msg = result.get("error", "No match")
                else:
                    status = result.get("status", {})
                    msg = status.get("msg", "No match")
                print(f"  [{i+1}/{total_chunks}] {start_time//60}:{start_time%60:02d} - {msg}")

            # Rate limiting
            time.sleep(0.5 if USE_SHAZAM else 0.2)

    # Apply confidence filtering
    print("\nApplying confidence filters...")
    tracklist = apply_confidence_filter(raw_matches)

    # Save results
    output = {
        "set_name": set_name,
        "source_file": mp4_path,
        "duration_seconds": duration,
        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "api_used": "shazam" if USE_SHAZAM else "acrcloud",
        "settings": {
            "chunk_duration": chunk_dur,
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
    global USE_SHAZAM

    # Parse arguments
    args = sys.argv[1:]

    # Check for API flag
    if "--acr" in args:
        USE_SHAZAM = False
        args.remove("--acr")
    elif "--shazam" in args:
        USE_SHAZAM = True
        args.remove("--shazam")
    # Default is Shazam (USE_SHAZAM = True)

    if len(args) < 1:
        print("Usage: python track_identifier.py [--shazam|--acr] <mp4_file> [output.json]")
        print("\nOptions:")
        print("  --shazam  Use Shazam API (default, larger database)")
        print("  --acr     Use ACRCloud API")
        print("\nExample:")
        print("  python track_identifier.py /root/Andrea.mp4")
        print("  python track_identifier.py --acr /root/Andrea.mp4 andrea_tracks.json")
        sys.exit(1)

    mp4_path = args[0]
    output_json = args[1] if len(args) > 1 else None

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
