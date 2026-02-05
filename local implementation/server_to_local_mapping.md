Server-to-Local Mapping
=======================

This document will be finalized after running the server snapshot plan.
Placeholders are based on the current repo configuration.

Path Mappings (From Snapshot)
-----------------------------
- Server: `/var/www/html/hls`
  Local:  `local implementation/hls`

- Server: `/root/now_playing.txt`
  Local:  `local implementation/run/now_playing.txt`

- Server: `/var/run/stream.lock`
  Local:  `local implementation/run/stream.lock`

- Server: `/etc/nginx/nginx.conf` and `/etc/nginx/sites-enabled/*`
  Local:  `local implementation/nginx_local.conf`

- Server: `/root/*.mp4`
  Local:  `/Users/andreabenedetti/Movies`

- Server repo location: `/root/ronaut-radio-app`
  Local repo location: repo root

Runtime Mapping
---------------
- RTMP app name:
  Server: `live`
  Local:  `live`

- RTMP publish URL:
  Server: `rtmp://<server-ip>/live/stream`
  Local:  `rtmp://localhost:1935/live/stream`

- HLS URL:
  Server: `https://stream.ronautradio.la/live/hls/stream.m3u8`
  Local:  `http://localhost:8080/live/hls/stream.m3u8`

- API route:
  Server: `/api/now-playing` -> Flask `/now-playing`
  Local:  `http://localhost:5050/now-playing`

Snapshot Notes (Observed)
-------------------------
- HLS params:
  - `hls_fragment 4s`
  - `hls_playlist_length 60s`
  - `hls_cleanup on`

- FFmpeg (running):
  - 60 fps, GOP 120
  - Video: 3500k, maxrate 3500k, bufsize 7000k
  - Audio: AAC-LC, 512k, 48000 Hz
  - Uses `setpts`, `aresample`, `vsync 1`

- Logs:
  - Nginx: `/var/log/nginx/access.log`, `/var/log/nginx/error.log`
  - FFmpeg: `/root/ffmpeg_random_stream.log`
