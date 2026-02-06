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
    """Extract raw PCM audio chunk for Shazam (44100Hz mono 16-bit)."""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_time),
        "-i", mp4_path,
        "-t", str(duration),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "44100",
        "-ac", "1",
        "-f", "s16le",  # Raw PCM, no header
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
    """Parse Shazam v3 response into a simplified track dict."""
    if "error" in result:
        return None

    # Check for matches in the v3 API response format
    matches = result.get("results", {}).get("matches", [])
    if not matches:
        return None

    # Get the first match ID
    shazam_id = matches[0].get("id", "")

    # Extract info from resources
    resources = result.get("resources", {})
    albums = resources.get("albums", {})
    artists_data = resources.get("artists", {})
    genres_data = resources.get("genres", {})

    # Get album info (contains artist name and album name)
    album_info = {}
    for album in albums.values():
        attrs = album.get("attributes", {})
        if attrs:
            album_info = attrs
            break

    # Get artist info
    artist_name = ""
    for artist in artists_data.values():
        attrs = artist.get("attributes", {})
        if attrs.get("name"):
            artist_name = attrs["name"]
            break

    # Get genres
    genres = []
    for genre in genres_data.values():
        attrs = genre.get("attributes", {})
        if attrs.get("name") and attrs["name"] != "Music":  # Skip generic "Music"
            genres.append(attrs["name"])

    # The album's artistName often has the full artist list
    full_artist = album_info.get("artistName", artist_name)
    album_name = album_info.get("name", "").replace(" - Single", "")  # Clean up single suffix
    release_date = album_info.get("releaseDate", "")

    if not full_artist and not album_name:
        return None

    return {
        "acrid": f"shazam_{shazam_id}",
        "title": album_name,
        "artists": [full_artist] if full_artist else [],
        "album": album_name,
        "label": "",
        "release_date": release_date,
        "duration_ms": 0,
        "score": 100,  # Shazam match = high confidence
        "play_offset_ms": 0,
        "spotify_id": None,
        "deezer_id": None,
        "external_ids": {},
        "shazam_id": shazam_id,
        "genres": genres,
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
                chunk_path = os.path.join(tmpdir, f"chunk_{i:04d}.raw")
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

    # Aggregate genres from all tracks to determine set vibe
    from collections import Counter
    all_genres = []
    for track in tracklist:
        all_genres.extend(track.get("genres", []))
    genre_counts = Counter(all_genres)
    top_genres = [g for g, _ in genre_counts.most_common(5)]  # Top 5 genres

    # Build unidentified tracks list (gaps between identified tracks)
    unidentified = build_unidentified_tracks(tracklist, raw_matches, duration, CHUNK_INTERVAL)

    # Save results
    output = {
        "set_name": set_name,
        "source_file": mp4_path,
        "duration_seconds": duration,
        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "api_used": "shazam" if USE_SHAZAM else "acrcloud",
        "genres": top_genres,  # Aggregated set genres/vibes
        "settings": {
            "chunk_duration": chunk_dur,
            "chunk_interval": CHUNK_INTERVAL,
            "min_consecutive": MIN_CONSECUTIVE,
            "max_popularity": MAX_POPULARITY,
            "min_confidence": MIN_CONFIDENCE,
        },
        "raw_matches": raw_matches,
        "tracklist": tracklist,
        "unidentified": unidentified,
    }

    with open(output_json, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to: {output_json}")
    print(f"Found {len(tracklist)} confirmed tracks")

    return tracklist


def build_unidentified_tracks(tracklist: list, raw_matches: list, duration: int, interval: int) -> list:
    """
    Build list of unidentified sections for crowd-sourcing.
    Shows time ranges where songs weren't identified, with estimated track count.
    Typical DJ set track: 4-6 minutes average.
    """
    if duration <= 0:
        return []

    AVG_TRACK_LENGTH = 300  # 5 minutes - typical DJ set track length
    MIN_GAP_TO_REPORT = 120  # Only report gaps > 2 minutes

    # Get identified track time ranges from actual start/end times
    identified_ranges = []
    for t in tracklist:
        start = t["start_time"]
        end = t.get("end_time", start + AVG_TRACK_LENGTH)  # Use actual end_time if available
        identified_ranges.append((start, end))

    # Sort and merge overlapping ranges
    identified_ranges.sort()
    merged = []
    for start, end in identified_ranges:
        if merged and start <= merged[-1][1] + 60:  # Allow 60s gap for transitions
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Find gaps between identified tracks
    unidentified = []
    prev_end = 0

    for start, end in merged:
        gap_duration = start - prev_end
        if gap_duration > MIN_GAP_TO_REPORT:
            # Estimate number of tracks in this gap
            estimated_tracks = max(1, round(gap_duration / AVG_TRACK_LENGTH))
            unidentified.append({
                "start_time": prev_end,
                "end_time": start,
                "start_time_formatted": f"{prev_end//60}:{prev_end%60:02d}",
                "end_time_formatted": f"{start//60}:{start%60:02d}",
                "duration_seconds": gap_duration,
                "estimated_tracks": estimated_tracks,
                "title": f"~{estimated_tracks} unidentified track{'s' if estimated_tracks > 1 else ''}",
                "artists": ["Help ID"],
                "needs_id": True,
            })
        prev_end = end

    # Gap after last identified track
    if prev_end < duration - MIN_GAP_TO_REPORT:
        gap_duration = duration - prev_end
        estimated_tracks = max(1, round(gap_duration / AVG_TRACK_LENGTH))
        unidentified.append({
            "start_time": prev_end,
            "end_time": duration,
            "start_time_formatted": f"{prev_end//60}:{prev_end%60:02d}",
            "end_time_formatted": f"{duration//60}:{duration%60:02d}",
            "duration_seconds": gap_duration,
            "estimated_tracks": estimated_tracks,
            "title": f"~{estimated_tracks} unidentified track{'s' if estimated_tracks > 1 else ''}",
            "artists": ["Help ID"],
            "needs_id": True,
        })

    # If no tracks identified at all, show entire set as unidentified
    if not tracklist:
        estimated_tracks = max(1, round(duration / AVG_TRACK_LENGTH))
        return [{
            "start_time": 0,
            "end_time": duration,
            "start_time_formatted": "0:00",
            "end_time_formatted": f"{duration//60}:{duration%60:02d}",
            "duration_seconds": duration,
            "estimated_tracks": estimated_tracks,
            "title": f"~{estimated_tracks} unidentified tracks (full set)",
            "artists": ["Help ID"],
            "needs_id": True,
        }]

    return unidentified


def apply_confidence_filter(raw_matches: list) -> list:
    """
    Filter raw matches using:
    1. Minimum confidence score
    2. Consecutive match requirement (identifies song start AND end)
    3. Track end detection via match gaps or new track detection
    4. Deduplication - each track appears only once (first occurrence)
    """
    if not raw_matches:
        return []

    # Filter by minimum confidence
    confident_matches = [
        m for m in raw_matches
        if m["track"]["score"] >= MIN_CONFIDENCE
    ]

    # Track already-added songs to avoid duplicates
    seen_acrids = set()

    # Group consecutive matches by acrid to find song boundaries
    tracklist = []
    current_track = None
    consecutive_count = 0
    first_seen_time = 0
    last_seen_time = 0

    for match in confident_matches:
        acrid = match["track"]["acrid"]
        match_time = match["start_time"]

        if current_track and current_track["acrid"] == acrid:
            # Same track - check if it's truly consecutive (within ~60s gap)
            if match_time - last_seen_time <= 60:
                consecutive_count += 1
                last_seen_time = match_time
            else:
                # Gap too large - this is probably a different occurrence
                # Save current track first if valid AND not already seen
                if consecutive_count >= MIN_CONSECUTIVE and current_track["acrid"] not in seen_acrids:
                    end_time = last_seen_time + CHUNK_INTERVAL
                    tracklist.append({
                        "start_time": first_seen_time,
                        "end_time": end_time,
                        "start_time_formatted": f"{first_seen_time//60}:{first_seen_time%60:02d}",
                        "end_time_formatted": f"{end_time//60}:{end_time%60:02d}",
                        "duration_seconds": end_time - first_seen_time,
                        "acrid": current_track["acrid"],
                        "title": current_track["title"],
                        "artists": current_track["artists"],
                        "album": current_track["album"],
                        "label": current_track["label"],
                        "genres": current_track.get("genres", []),
                        "consecutive_matches": consecutive_count,
                        "confidence": "high" if consecutive_count >= 3 else "medium",
                    })
                    seen_acrids.add(current_track["acrid"])
                # Start fresh occurrence of same track
                first_seen_time = match_time
                last_seen_time = match_time
                consecutive_count = 1
        else:
            # Different track - save previous if valid AND not already seen
            if current_track and consecutive_count >= MIN_CONSECUTIVE and current_track["acrid"] not in seen_acrids:
                end_time = last_seen_time + CHUNK_INTERVAL
                tracklist.append({
                    "start_time": first_seen_time,
                    "end_time": end_time,
                    "start_time_formatted": f"{first_seen_time//60}:{first_seen_time%60:02d}",
                    "end_time_formatted": f"{end_time//60}:{end_time%60:02d}",
                    "duration_seconds": end_time - first_seen_time,
                    "acrid": current_track["acrid"],
                    "title": current_track["title"],
                    "artists": current_track["artists"],
                    "album": current_track["album"],
                    "label": current_track["label"],
                    "genres": current_track.get("genres", []),
                    "consecutive_matches": consecutive_count,
                    "confidence": "high" if consecutive_count >= 3 else "medium",
                })
                seen_acrids.add(current_track["acrid"])

            # Start tracking new track
            current_track = match["track"]
            consecutive_count = 1
            first_seen_time = match_time
            last_seen_time = match_time

    # Don't forget the last track (if not already seen)
    if current_track and consecutive_count >= MIN_CONSECUTIVE and current_track["acrid"] not in seen_acrids:
        end_time = last_seen_time + CHUNK_INTERVAL
        tracklist.append({
            "start_time": first_seen_time,
            "end_time": end_time,
            "start_time_formatted": f"{first_seen_time//60}:{first_seen_time%60:02d}",
            "end_time_formatted": f"{end_time//60}:{end_time%60:02d}",
            "duration_seconds": end_time - first_seen_time,
            "acrid": current_track["acrid"],
            "title": current_track["title"],
            "artists": current_track["artists"],
            "album": current_track["album"],
            "label": current_track["label"],
            "genres": current_track.get("genres", []),
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
