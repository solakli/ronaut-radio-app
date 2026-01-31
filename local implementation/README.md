Local Implementation (macOS)
============================

Goal
----
This folder provides a fully local, localhost-only implementation of the
streaming stack so you can debug end-to-end on your Mac without touching the
live server. It streams ~30 seconds per MP4 from:

  /Users/andreabenedetti/Movies

Architecture (Local)
--------------------
1) `start_stream_local_smart.sh` streams 30-second clips via RTMP.
2) Local Nginx (with RTMP module) receives RTMP and generates HLS segments.
3) Nginx serves HLS over HTTP on port 8080.
4) `now_playing_api.py` serves the current track on port 5050.

Prerequisites (macOS)
---------------------
- Nginx with RTMP module
- FFmpeg (includes ffprobe)
- Python 3 + venv or uv
- Node.js is NOT required for local streaming

Install hints (brief)
---------------------
- FFmpeg:
  brew install ffmpeg

- Nginx with RTMP:
  - Option A (brew, if available): brew install nginx-full --with-rtmp
  - Option B (docker): use an nginx-rtmp container (see Troubleshooting).

- Python:
  brew install python

Quick start (3 commands max)
----------------------------
1) Setup:
   ./local\ implementation/run_local.sh setup

2) Run:
   ./local\ implementation/run_local.sh

3) Stop/cleanup:
   ./local\ implementation/stop_local.sh

URLs (Local)
------------
- HLS playlist:
  http://localhost:8080/hls/stream.m3u8
- Now Playing API:
  http://localhost:5050/now-playing

Logs and Runtime Files
----------------------
- Nginx logs: local implementation/logs/nginx_access.log, nginx_error.log
- Streamer log: local implementation/logs/ffmpeg.log
- API log: local implementation/logs/api.log
- Runtime state: local implementation/run/

Troubleshooting
---------------
- "nginx: unknown directive rtmp" or "module not found":
  Your nginx build does not include the RTMP module. Either install an RTMP
  build or use Docker:
    docker run --rm -it -p 1935:1935 -p 8080:8080 \
      -v "$(pwd)/local implementation/hls:/opt/nginx/hls" \
      alfg/nginx-rtmp
  If using Docker, skip local nginx startup in `run_local.sh` and point RTMP
  to rtmp://localhost/live/stream.

- "Port already in use":
  Change ports via env vars:
    HTTP_PORT=8081 RTMP_PORT=1936 API_PORT=5051 ./local\ implementation/run_local.sh

- "No MP4 files found":
  Ensure `/Users/andreabenedetti/Movies` contains .mp4 files.

Server Transition Copies
------------------------
These are unmodified copies of production-facing files for reference and
eventual deployment transition:

- `server_transition_copies/nginx.conf` -> main Nginx + RTMP config
- `server_transition_copies/ronautradio.la.conf` -> site vhost config
- `server_transition_copies/start_stream_smart.sh` -> production streamer
- `server_transition_copies/now_playing_api.py` -> production API
