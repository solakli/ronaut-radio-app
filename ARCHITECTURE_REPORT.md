Ronaut Radio App - Architecture Report
======================================

Overview
--------
This repository implements a compact streaming stack for a radio-style
continuous video stream. The core flow is:

1) Local MP4 files -> FFmpeg publishes RTMP
2) Nginx RTMP module generates HLS segments
3) Nginx serves HLS and proxies a "now playing" API
4) HTML pages play the HLS stream

Main Component
--------------
The main component is the streaming pipeline driven by
`start_stream_smart.sh` and the Nginx RTMP/HLS configuration. This pair
builds playlists, publishes RTMP, converts to HLS, and serves it to clients.

Architecture Details
--------------------
- Ingest and transcode:
  `start_stream_smart.sh` builds a weighted playlist from `/root/*.mp4`,
  then runs a single long-lived FFmpeg process to publish RTMP. It tracks
  play history to bias the playlist fairly and by recency.

- Streaming server:
  `nginx.conf` includes an RTMP block that accepts the RTMP publish and
  generates HLS segments under `/var/www/html/hls`.

- HTTP serving and proxy:
  Nginx serves HLS at `/live/hls/` with CORS headers and proxies a Flask API
  at `/api/now-playing`. It also proxies the main website to a Node.js app
  on port 3000 (the Node.js app is not in this repo).

- Now Playing API:
  `now_playing_api.py` exposes `/now-playing` and reads
  `/root/now_playing.txt` for the current track name.

- Clients:
  HTML files use HLS.js or native HLS playback to render the stream.

Scripts and Files (Function + Intent)
-------------------------------------
- `start_stream_smart.sh`
  - Purpose: robust, long-running streamer with playlist weighting and
    restart logic.
  - Behavior:
    - Builds playlists based on recency and fairness using a state file.
    - Writes concat input for FFmpeg and publishes RTMP.
    - Supervises FFmpeg, rebuilds on exit, retries on failure.

- `start_stream.sh`
  - Purpose: simpler streamer for looping MP4s.
  - Behavior:
    - Builds a static playlist from `/root/*.mp4`.
    - Uses per-file FFmpeg `-c copy` publish to RTMP.
    - Updates `/root/now_playing.txt` per file.

- `now_playing_api.py`
  - Purpose: small API that returns the current track name as JSON.
  - Behavior: reads `/root/now_playing.txt` and responds to
    `/now-playing`.

- `validate_and_convert_mp4s.sh`
  - Purpose: validate and normalize MP4 files for streaming.
  - Behavior: uses ffprobe to inspect codecs, re-encodes to H.264 + AAC if
    needed.

- `rekordbox-path-correct.py`
  - Purpose: fix broken file paths in a Rekordbox XML export.
  - Behavior: rewrites `Location` attributes to valid local file paths and
    writes `rekordbox_fixed.xml`.

- `nginx.conf`
  - Purpose: primary Nginx configuration for RTMP ingest, HLS generation,
    HTTPS termination, and proxying.
  - Notes: uses Let's Encrypt certificates for `stream.ronautradio.la`.

- `nginx_backup.conf`
  - Purpose: backup variant of the Nginx configuration.
  - Notes: uses self-signed certificates and slightly different HLS settings.

- `ronautradio.la.conf`
  - Purpose: site-specific Nginx vhost for `ronautradio.la`.

- `stream-test_lp.html`
  - Purpose: full-screen player with overlay and ticker; polls the
    "now playing" API.

- `stream-test.html`
  - Purpose: HLS player with Livepeer primary and VPS fallback.

- `test1.html`
  - Purpose: minimal HLS test player.

- `now_playing.txt`
  - Purpose: runtime file that stores the current track name (written by
    streaming scripts, read by the API).

- `watchh_dog`
  - Purpose: empty placeholder file (no behavior).

Acronyms Used
------------
- AAC: Advanced Audio Coding.
- API: Application Programming Interface.
- CORS: Cross-Origin Resource Sharing.
- FFmpeg: Fast Forward MPEG (the multimedia framework).
- FFprobe: FFmpeg's media inspection tool.
- HLS: HTTP Live Streaming.
- HTTP: Hypertext Transfer Protocol.
- HTTPS: HTTP Secure (HTTP over TLS).
- H.264: Advanced Video Coding (video compression standard).
- MP4: MPEG-4 Part 14 (container format).
- RTMP: Real-Time Messaging Protocol.
- TLS: Transport Layer Security.
- VPS: Virtual Private Server.

Notes and Assumptions
---------------------
- The Node.js application proxied at `http://localhost:3000` is not part of
  this repository.
- Paths like `/root/*.mp4` and `/var/www/html/hls` reflect the server
  deployment environment, not the repo layout.
