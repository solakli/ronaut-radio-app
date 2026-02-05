import json
import os
import subprocess
import time

from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- Configuration ---
NOW_PLAYING_JSON = "/root/now_playing.json"
PLAY_LOG_FILE = "/root/play_log.tsv"
PLAYLIST_FILE = "/root/playlist.txt"
DURATIONS_FILE = "/root/durations.txt"
HLS_M3U8 = "/var/www/html/hls/stream.m3u8"
LIVE_MODE_FLAG = "/root/.live_mode"

HEARTBEAT_STALE_SECONDS = 20
HLS_STALE_SECONDS = 15


def _file_age(path):
    """Return age of file in seconds, or infinity if missing."""
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return float("inf")


def _detect_mode():
    """
    Determine stream mode:
      'auto'    - ffmpeg is streaming pre-recorded sets
      'live'    - OBS (or another external source) is streaming
      'offline' - nothing is streaming
    """
    # If the live flag is set, always report live
    if os.path.isfile(LIVE_MODE_FLAG):
        return "live"

    hls_fresh = _file_age(HLS_M3U8) < HLS_STALE_SECONDS
    heartbeat_fresh = False

    try:
        with open(NOW_PLAYING_JSON, "r") as f:
            data = json.load(f)
        heartbeat_age = time.time() - data.get("heartbeat", 0)
        heartbeat_fresh = heartbeat_age < HEARTBEAT_STALE_SECONDS
    except (OSError, json.JSONDecodeError, KeyError):
        pass

    if hls_fresh and heartbeat_fresh:
        return "auto"
    elif hls_fresh and not heartbeat_fresh:
        return "live"
    else:
        return "offline"


def _display_name(filepath):
    """Extract display name from a file path (basename minus .mp4)."""
    name = os.path.basename(filepath)
    if name.endswith(".mp4"):
        name = name[:-4]
    return name


# --- Endpoints ---

@app.route("/now-playing")
def now_playing():
    mode = _detect_mode()

    if mode == "live":
        return jsonify(now_playing="LIVE", mode="live", started_at=0, file="")

    if mode == "offline":
        return jsonify(now_playing="Offline", mode="offline", started_at=0, file="")

    try:
        with open(NOW_PLAYING_JSON, "r") as f:
            data = json.load(f)
        return jsonify(
            now_playing=data.get("display_name", "Unknown"),
            mode="auto",
            started_at=data.get("started_at", 0),
            file=data.get("file", ""),
        )
    except (OSError, json.JSONDecodeError):
        return jsonify(now_playing="No track playing", mode="auto", started_at=0, file="")


@app.route("/programme")
def programme():
    mode = _detect_mode()

    if mode != "auto":
        return jsonify(mode=mode, current=None, upcoming=[])

    count = min(int(request.args.get("count", 10)), 50)

    # Load now-playing state
    try:
        with open(NOW_PLAYING_JSON, "r") as f:
            np_data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return jsonify(mode=mode, current=None, upcoming=[])

    # Load playlist
    try:
        with open(PLAYLIST_FILE, "r") as f:
            playlist = [line.strip() for line in f if line.strip()]
    except OSError:
        return jsonify(mode=mode, current=None, upcoming=[])

    if not playlist:
        return jsonify(mode=mode, current=None, upcoming=[])

    # Load durations: path -> seconds
    durations = {}
    try:
        with open(DURATIONS_FILE, "r") as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) >= 2:
                    try:
                        durations[parts[0]] = int(float(parts[1]))
                    except ValueError:
                        pass
    except OSError:
        pass

    # Find current position
    current_file = np_data.get("file", "")
    current_idx = np_data.get("playlist_index", 0)

    if current_idx < 0 or current_idx >= len(playlist):
        current_idx = 0

    # Validate index matches file; if not, search
    if playlist[current_idx].strip() != current_file:
        for i, p in enumerate(playlist):
            if p.strip() == current_file:
                current_idx = i
                break

    current_dur = durations.get(current_file, 0)
    started_at = np_data.get("started_at", 0)

    current = {
        "display_name": _display_name(current_file) if current_file else "Unknown",
        "started_at": started_at,
        "duration": current_dur,
    }

    # Build upcoming list with estimated start times
    upcoming = []
    next_start = (started_at + current_dur) if (started_at > 0 and current_dur > 0) else int(time.time())

    for offset in range(1, count + 1):
        idx = (current_idx + offset) % len(playlist)
        track_file = playlist[idx].strip()
        dur = durations.get(track_file, 0)
        upcoming.append({
            "display_name": _display_name(track_file),
            "estimated_start": next_start,
            "duration": dur,
        })
        next_start += dur

    return jsonify(mode=mode, current=current, upcoming=upcoming)


@app.route("/play-log")
def play_log():
    summary_mode = request.args.get("summary", "0") == "1"
    days = request.args.get("days", None)

    cutoff = 0
    if days is not None:
        try:
            cutoff = time.time() - (int(days) * 86400)
        except ValueError:
            cutoff = 0

    entries = []
    try:
        with open(PLAY_LOG_FILE, "r") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) < 2:
                    continue
                try:
                    ts = int(parts[0])
                except ValueError:
                    continue
                if cutoff and ts < cutoff:
                    continue
                entries.append({
                    "timestamp": ts,
                    "file": parts[1],
                    "display_name": _display_name(parts[1]),
                })
    except OSError:
        pass

    if not summary_mode:
        return jsonify(entries=entries)

    # Aggregate play counts
    agg = {}
    for e in entries:
        key = e["file"]
        if key not in agg:
            agg[key] = {
                "file": key,
                "display_name": e["display_name"],
                "play_count": 0,
                "first_played": e["timestamp"],
                "last_played": e["timestamp"],
            }
        agg[key]["play_count"] += 1
        if e["timestamp"] < agg[key]["first_played"]:
            agg[key]["first_played"] = e["timestamp"]
        if e["timestamp"] > agg[key]["last_played"]:
            agg[key]["last_played"] = e["timestamp"]

    summary = sorted(agg.values(), key=lambda x: x["play_count"], reverse=True)
    return jsonify(summary=summary)


@app.route("/go-live", methods=["POST"])
def go_live():
    # Create live mode flag
    with open(LIVE_MODE_FLAG, "w") as f:
        f.write(str(int(time.time())))

    # Kill ffmpeg concat process so OBS can take the RTMP slot
    try:
        subprocess.run(
            ["pkill", "-f", "ffmpeg.*concat"],
            timeout=5,
            check=False,
        )
    except Exception:
        pass

    return jsonify(status="ok", mode="live")


@app.route("/stop-live", methods=["POST"])
def stop_live():
    # Remove live mode flag — supervisor will auto-restart ffmpeg
    try:
        os.remove(LIVE_MODE_FLAG)
    except OSError:
        pass

    return jsonify(status="ok", mode="offline")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
