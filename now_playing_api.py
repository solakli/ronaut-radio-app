import json
import os
import re
import subprocess
import time

import requests as http_requests
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
STAFF_PICKS_FILE = "/root/staff_picks.json"
RESIDENTS_FILE = "/root/residents.json"

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
    """Extract display name from a file path, format as '001 - Name'."""
    name = os.path.basename(filepath)
    if name.endswith(".mp4"):
        name = name[:-4]
    # Extract index from "Ronaut[XXX]-Name" format and reformat as "XXX - Name"
    match = re.match(r"^Ronaut\[(\d+)\]\s*[-_]\s*(.+)$", name, flags=re.IGNORECASE)
    if match:
        index = match.group(1)
        title = match.group(2)
        return f"{index} - {title}"
    return name


def _normalize_set_name(name):
    """Normalize a set name to match renamed files (case/spacing/prefix agnostic)."""
    base = os.path.basename(name or "")
    if base.lower().endswith(".mp4"):
        base = base[:-4]
    base = base.strip()
    # Strip leading "Ronaout[###]-" (or similar) prefixes
    base = re.sub(r"^ronaut\[\d+\]\s*[-_ ]*", "", base, flags=re.IGNORECASE)
    # Remove non-alphanumeric to make comparisons resilient to punctuation/spaces
    base = re.sub(r"[^a-z0-9]+", "", base.lower())
    return base


def _build_mp4_index(root_dir="/root"):
    """Map normalized names -> actual filenames present on disk."""
    index = {}
    try:
        for entry in os.listdir(root_dir):
            if entry.lower().endswith(".mp4"):
                norm = _normalize_set_name(entry)
                index.setdefault(norm, entry)
    except OSError:
        pass
    return index


def _resolve_filename(fname, mp4_index, fallback_name=""):
    """Resolve an incoming filename/title to a real file in /root."""
    if fname:
        fname = os.path.basename(fname)
        if os.path.isfile(os.path.join("/root", fname)):
            return fname
    candidate = fname or fallback_name
    if not candidate:
        return fname or ""
    norm = _normalize_set_name(candidate)
    return mp4_index.get(norm, fname or "")


def _load_tracklist(name_candidates):
    """Try loading tracklists for any of the candidate names."""
    for name in name_candidates:
        tracklist_path = f"/root/tracklists/{name}_tracklist.json"
        try:
            with open(tracklist_path, "r") as f:
                tl_data = json.load(f)
                return (
                    tl_data.get("tracklist", []),
                    tl_data.get("unidentified", []),
                    tl_data.get("genres", []),
                )
        except (OSError, json.JSONDecodeError):
            continue
    return [], [], []


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

    # Build recent list (last 2-3 tracks that played)
    recent_count = min(int(request.args.get("recent", 3)), 5)
    recent = []
    prev_start = started_at
    for offset in range(1, recent_count + 1):
        idx = (current_idx - offset) % len(playlist)
        track_file = playlist[idx].strip()
        dur = durations.get(track_file, 0)
        prev_start -= dur
        recent.insert(0, {
            "display_name": _display_name(track_file),
            "ended_at": prev_start + dur,
            "duration": dur,
        })

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

    return jsonify(mode=mode, current=current, recent=recent, upcoming=upcoming)


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
    # Remove live mode flag
    try:
        os.remove(LIVE_MODE_FLAG)
    except OSError:
        pass

    # Restart the automated stream
    _restart_auto_stream()

    return jsonify(status="ok", mode="auto")


def _restart_auto_stream():
    """Restart the automated ffmpeg streaming script."""
    try:
        # Kill any existing stream first
        subprocess.run(["pkill", "-f", "start_stream"], timeout=5, check=False)
        time.sleep(1)
        # Start the stream in background
        subprocess.Popen(
            ["nohup", "/root/start_stream_smart.sh"],
            stdout=open("/root/stream.log", "a"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        print(f"Error restarting stream: {e}")


# --- RTMP Callbacks (called by nginx when OBS connects/disconnects) ---

@app.route("/rtmp-publish", methods=["POST"])
def rtmp_publish():
    """
    Called by nginx when someone starts streaming to RTMP.
    This triggers when OBS connects - automatically go live.
    """
    # Get stream info from nginx
    stream_name = request.form.get("name", "unknown")
    client_addr = request.form.get("addr", "unknown")

    print(f"RTMP publish: stream={stream_name} from {client_addr}")

    # Check if this is OBS (external) or our ffmpeg (internal)
    # Our ffmpeg streams from localhost, OBS streams from external IP
    if client_addr.startswith("127.") or client_addr == "localhost":
        # This is our automated ffmpeg, allow it
        return "OK", 200

    # External connection (OBS) - go live!
    with open(LIVE_MODE_FLAG, "w") as f:
        f.write(str(int(time.time())))

    # Kill the automated ffmpeg stream
    try:
        subprocess.run(["pkill", "-f", "ffmpeg.*concat"], timeout=5, check=False)
    except Exception:
        pass

    # Send Discord notification
    try:
        from discord_notifier import notify_live
        notify_live()
    except Exception as e:
        print(f"Discord notification failed: {e}")

    print("OBS connected - went live!")
    return "OK", 200


@app.route("/rtmp-done", methods=["POST"])
def rtmp_done():
    """
    Called by nginx when someone stops streaming to RTMP.
    This triggers when OBS disconnects - automatically resume auto stream.
    """
    stream_name = request.form.get("name", "unknown")
    client_addr = request.form.get("addr", "unknown")

    print(f"RTMP done: stream={stream_name} from {client_addr}")

    # Only act if this was an external stream (OBS)
    if client_addr.startswith("127.") or client_addr == "localhost":
        # Our ffmpeg stopped, probably killed - don't restart yet
        return "OK", 200

    # OBS disconnected - remove live mode and restart auto stream
    try:
        os.remove(LIVE_MODE_FLAG)
    except OSError:
        pass

    # Send Discord notification
    try:
        from discord_notifier import notify_end_live
        notify_end_live()
    except Exception as e:
        print(f"Discord notification failed: {e}")

    # Restart automated stream
    _restart_auto_stream()

    print("OBS disconnected - restarting auto stream!")
    return "OK", 200


@app.route("/sets")
def sets():
    mp4_index = _build_mp4_index()
    try:
        with open(STAFF_PICKS_FILE, "r") as f:
            picks = json.load(f)
    except (OSError, json.JSONDecodeError):
        return jsonify(sets=[])

    result = []
    for pick in picks:
        raw_fname = pick.get("filename", "")
        resolved_fname = _resolve_filename(raw_fname, mp4_index, pick.get("title", ""))
        fname = resolved_fname or raw_fname
        fname = os.path.basename(fname) if fname else ""
        name = _display_name(fname) if fname else ""

        # Load tracklist if available (try resolved name, then raw name)
        candidates = []
        if name:
            candidates.append(name)
        raw_name = _display_name(raw_fname) if raw_fname else ""
        if raw_name and raw_name not in candidates:
            candidates.append(raw_name)
        tracklist, unidentified, genres = _load_tracklist(candidates)

        result.append({
            "filename": fname,
            "title": pick.get("title", name),
            "description": pick.get("description", ""),
            "thumbnail": "/sets/thumbs/{}.jpg".format(name or raw_name),
            "url": "/sets/{}".format(fname),
            "genres": genres,
            "tracklist": tracklist,
            "unidentified": unidentified,
        })
    return jsonify(sets=result)


# --- Residents ---
@app.route("/residents")
def residents():
    """Return list of featured residents."""
    mp4_index = _build_mp4_index()
    try:
        with open(RESIDENTS_FILE, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return jsonify(residents=[])

    result = []
    for resident in data:
        resolved_sets = []
        for s in resident.get("sets", []):
            resolved = _resolve_filename(s, mp4_index, "")
            resolved_sets.append(resolved or s)
        result.append({
            "name": resident.get("name", ""),
            "bio": resident.get("bio", ""),
            "photo": "/residents/{}".format(resident.get("photo", "")),
            "social": resident.get("social", {}),
            "sets": resolved_sets,  # List of MP4 filenames
        })
    return jsonify(residents=result)


# --- Track ID Submissions ---
TRACK_ID_DB = "/root/track_submissions.db"


def _get_submissions_db():
    """Get or create the track ID submissions database."""
    import sqlite3
    conn = sqlite3.connect(TRACK_ID_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        set_name TEXT NOT NULL,
        start_time INTEGER NOT NULL,
        artist TEXT NOT NULL,
        title TEXT NOT NULL,
        submitted_by TEXT,
        timestamp REAL NOT NULL,
        approved INTEGER DEFAULT 0
    )""")
    conn.commit()
    return conn


@app.route("/submit-id", methods=["POST"])
def submit_id():
    """Submit a track identification suggestion."""
    data = request.get_json() or {}

    set_name = (data.get("set_name") or "").strip()
    start_time = data.get("start_time", 0)
    artist = (data.get("artist") or "").strip()[:100]
    title = (data.get("title") or "").strip()[:200]
    submitted_by = (data.get("submitted_by") or "Anonymous").strip()[:50]

    if not set_name or not artist or not title:
        return jsonify(error="Missing required fields"), 400

    try:
        conn = _get_submissions_db()
        conn.execute(
            "INSERT INTO submissions (set_name, start_time, artist, title, submitted_by, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (set_name, start_time, artist, title, submitted_by, time.time())
        )
        conn.commit()
        conn.close()
        return jsonify(status="ok", message="Thanks for helping identify this track!")
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/submissions")
def get_submissions():
    """Get pending track ID submissions (for review)."""
    set_name = request.args.get("set")

    try:
        conn = _get_submissions_db()
        if set_name:
            rows = conn.execute(
                "SELECT id, set_name, start_time, artist, title, submitted_by, timestamp, approved FROM submissions WHERE set_name = ? ORDER BY start_time",
                (set_name,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, set_name, start_time, artist, title, submitted_by, timestamp, approved FROM submissions ORDER BY timestamp DESC LIMIT 100"
            ).fetchall()
        conn.close()

        submissions = [{
            "id": r[0],
            "set_name": r[1],
            "start_time": r[2],
            "artist": r[3],
            "title": r[4],
            "submitted_by": r[5],
            "timestamp": r[6],
            "approved": bool(r[7]),
        } for r in rows]

        return jsonify(submissions=submissions)
    except Exception as e:
        return jsonify(error=str(e)), 500


# --- Calendar Proxy (to avoid CORS issues) ---
CALENDAR_ICS_URL = "https://calendar.google.com/calendar/ical/ronautradio%40gmail.com/public/basic.ics"

@app.route("/calendar.ics")
def calendar_proxy():
    """Proxy Google Calendar ICS feed to avoid CORS issues."""
    try:
        resp = http_requests.get(CALENDAR_ICS_URL, timeout=10)
        return resp.text, 200, {"Content-Type": "text/calendar"}
    except Exception as e:
        return str(e), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050)
