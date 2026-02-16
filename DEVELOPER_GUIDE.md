# Ronaut Radio - Developer Guide

## Overview

Ronaut Radio is a 24/7 vinyl-only internet radio station streaming from Los Angeles. The architecture is split between a VPS (streaming/API) and Render (frontend hosting).

---

## Repositories

| Repo | URL | Purpose |
|------|-----|---------|
| **Backend** | `github.com/solakli/ronaut-radio-app` | Streaming scripts, Flask API, nginx config |
| **Frontend** | `github.com/solakli/ronaut-radio-website` | Static website (index.html) hosted on Render |

---

## Infrastructure

### VPS (Contabo)
- **IP**: `89.117.16.160`
- **OS**: Ubuntu 22.04
- **SSH**: `ssh root@89.117.16.160` (password: `Caswell123@`)
- **Domain**: `stream.ronautradio.la` (points to VPS)

### Frontend (Render)
- **Domain**: `ronautradio.la` (points to Render)
- **Auto-deploy**: Pushes to `ronaut-radio-website` main branch trigger deploy

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                           VPS (Contabo)                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   ffmpeg    │  │    nginx    │  │      Flask APIs         │  │
│  │  streaming  │──│  port 80/443│──│  - now_playing (5050)   │  │
│  │   script    │  │  port 1935  │  │  - chat_server (5051)   │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│         │                │                      │                │
│         ▼                ▼                      ▼                │
│    /root/*.mp4     HLS @ /hls/         SQLite @ /root/          │
│    (DJ sets)       stream.m3u8         chat.db                  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Render (Frontend)                         │
│                     ronautradio.la/index.html                    │
│              Fetches API from stream.ronautradio.la              │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Files on VPS (`/root/`)

| File | Purpose |
|------|---------|
| `now_playing_api.py` | Flask API (port 5050) - now-playing, programme, sets, go-live |
| `chat_server.py` | Flask-SocketIO chat (port 5051) |
| `streaming_script.sh` | ffmpeg concat loop → RTMP → HLS |
| `playlist.txt` | Order of MP4s for streaming |
| `durations.txt` | Pre-computed durations for programme timing |
| `now_playing.json` | Current track state (updated by fd_watcher) |
| `staff_picks.json` | Curated sets list for Staff Picks |
| `chat.db` | SQLite database for chat messages |
| `*.mp4` | DJ set recordings (2-12 GB each, 12 files) |
| `tracklists/*.json` | Auto-generated tracklists from Shazam |

---

## API Endpoints (stream.ronautradio.la)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/now-playing` | GET | Current mode (auto/live/offline), track info |
| `/api/programme` | GET | Current + upcoming schedule with times |
| `/api/play-log` | GET | History of played tracks |
| `/api/sets` | GET | Staff picks with tracklists and genres |
| `/api/go-live` | POST | Switch to OBS live mode |
| `/api/stop-live` | POST | Return to auto-rotation |
| `/ws/` | WebSocket | Chat via Socket.IO |

---

## Deployment Process

### Backend (VPS)
```bash
# 1. Push changes to GitHub
cd /path/to/streaming-app
git add . && git commit -m "message" && git push origin main

# 2. SSH to VPS and pull
ssh root@89.117.16.160
cd /root/ronaut-radio-app && git pull origin main

# 3. Copy files to running locations
cp now_playing_api.py /root/
cp chat_server.py /root/
cp nginx.conf /etc/nginx/nginx.conf

# 4. Reload/restart services
nginx -s reload
pkill -f now_playing_api && nohup python3 /root/now_playing_api.py > /root/api.log 2>&1 &
pkill -f chat_server && nohup python3 /root/chat_server.py > /root/chat.log 2>&1 &
```

### Frontend (Render)
```bash
# Just push to GitHub - Render auto-deploys
cd /path/to/ronaut-radio-website
git add . && git commit -m "message" && git push origin main
# Wait ~1 minute for Render to deploy
```

---

## Important Gotchas

1. **CORS**: Flask handles CORS via `CORS(app)`. Do NOT add `add_header Access-Control-Allow-Origin` in nginx - causes duplicate headers.

2. **File Locations**: After `git pull` on VPS, must `cp` files to `/root/` and `/etc/nginx/` - the running copies are separate from the repo.

3. **No Process Manager**: Flask, chat, and streaming run via `nohup` - they won't survive a reboot. Consider adding systemd services.

4. **Flask-SocketIO 5.x**: Requires `allow_unsafe_werkzeug=True` in `socketio.run()` for production.

5. **Expect Scripts**: When using expect for SSH automation, `$var` and `[...]` are interpreted. Use `scp` to upload files with complex content.

---

## Track Identification

The `track_identifier.py` script identifies tracks in DJ sets using audio fingerprinting.

### Usage
```bash
python3 track_identifier.py [--shazam|--acr] <mp4_file> [output.json]

# Examples
python3 track_identifier.py /root/Andrea.mp4
python3 track_identifier.py --shazam /root/Gerd.mp4 /root/tracklists/Gerd_tracklist.json
```

### APIs
- **Shazam (RapidAPI)**: Default, better for rare vinyl (~8x more tracks found)
- **ACRCloud**: Alternative, use `--acr` flag

### Output
- JSON file with tracklist, genres, timestamps
- Genres aggregated for set-level "vibes"

---

## Local Development

### Directory Structure
```
/Users/Penguin/Desktop/ronaut/
├── streaming-app/          # Backend repo (ronaut-radio-app)
│   ├── now_playing_api.py
│   ├── chat_server.py
│   ├── track_identifier.py
│   ├── nginx.conf
│   └── ...
└── ronaut-radio-website/   # Frontend repo
    └── website/
        └── index.html
```

### Testing Locally
- Frontend can be opened directly in browser
- API calls go to `stream.ronautradio.la` (production)
- For local API testing, run Flask on your machine and update fetch URLs

---

## Credentials

### VPS SSH
- Host: `89.117.16.160`
- User: `root`
- Password: `Caswell123@`

### Shazam API (RapidAPI)
- Host: `shazam.p.rapidapi.com`
- Key: `fa8045a805mshe489b3f3302c27ep143a5ajsnd9b2ead05c0e`

### ACRCloud
- Host: `identify-us-west-2.acrcloud.com`
- Access Key: `93223fda5f0ce3be9e9458c4c515284c`
- Access Secret: `fovjEDkP7QPHpe9oYffhSLfx4LmRSREi3FvGjC2b`

---

## Contact

- Website: ronautradio.la
- Instagram: @ronautradio
- Email: ronautradiola@hotmail.com
