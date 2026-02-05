Diff Checklist (Server vs Local)
================================

Must Match Exactly
------------------
- RTMP application name (e.g., `live`)
- FFmpeg publish URL path (e.g., `/live/stream`)
- HLS path in nginx (`hls_path`)
- HLS segment settings (fragment length, playlist length, cleanup)
- FFmpeg encoding flags (keyframe interval, bitrate, audio settings)
- API route naming and port (e.g., `/now-playing`, `:5050`)
- Lock file behavior (path and enforcement)

Can Differ Safely
-----------------
- TLS / certificates (local HTTP only)
- Domain names / DNS
- Absolute base paths (`/root` vs repo-relative paths)
- Service managers (systemd on server vs scripts locally)

Current Server Values (Snapshot)
--------------------------------
- RTMP app: `live`
- HLS params: `hls_fragment 4s`, `hls_playlist_length 60s`, `hls_cleanup on`
- HLS path: `/var/www/html/hls` (served at `/live/hls/`)
- API: `/api/now-playing` -> Flask `/now-playing`
- FFmpeg: 60fps, GOP 120, 3500k/7000k video, AAC-LC 512k @ 48kHz
