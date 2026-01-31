Server-to-Local Mapping
=======================

This document will be finalized after running the server snapshot plan.
Placeholders are based on the current repo configuration.

Path Mappings (Initial Assumptions)
-----------------------------------
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

Runtime Mapping
---------------
- RTMP app name:
  Server: `live`
  Local:  `live` (TODO: confirm from server)

- RTMP publish URL:
  Server: `rtmp://<server-ip>/live/stream`
  Local:  `rtmp://localhost:1935/live/stream`

- HLS URL:
  Server: `https://<domain>/live/hls/stream.m3u8`
  Local:  `http://localhost:8080/hls/stream.m3u8`

- API route:
  Server: `/api/now-playing` -> Flask `/now-playing`
  Local:  `http://localhost:5050/now-playing`

TODO After Snapshot
-------------------
- Confirm nginx include paths and RTMP block configuration.
- Confirm exact HLS parameters (fragment length, playlist length, cleanup).
- Confirm FFmpeg flags from production start scripts.
- Confirm log file locations and formats.
