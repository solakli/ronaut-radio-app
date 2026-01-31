# Ronaut — Internal README (Infra & Ops)

> ⚠️ **INTERNAL ONLY**
> This document contains infrastructure details. Do NOT make this repository public.

---

## Project Overview
Ronaut is a VPS-hosted online radio platform that streams:
- **Pre-recorded DJ sets** (MP4)
- **Live DJ streams** (via OBS)

The system uses lightweight shell scripts, cron jobs, and a streaming server to automate playback while allowing manual live overrides.

---

## Server Access

- **VPS IP:** 89.117.16.160  
- **User:** root  
- **Access:** SSH key / password (shared privately)

```bash
ssh root@89.117.16.160
```

---

## Directory Structure

```text
/root
├── streaming-app/        # main codebase
├── *.mp4                 # recorded DJ sets
├── playlists/            # playlist definitions
├── logs/                 # logs
├── health_check.sh       # cron watchdog
├── start_stream.sh       # basic stream starter
├── start_stream_smart.sh # safe stream starter (preferred)
├── stop_stream.sh        # stops active streams
```

---

## Media / DJ Sets

**Location**
```bash
/root/*.mp4
```

Example:
```text
Andrea.mp4
Emami.mp4
Daiup.mp4
```

---

## Streaming Modes

### Recorded Mode
- Plays MP4 DJ sets
- Can rotate automatically
- Default idle mode

### Live Mode
- Started manually via OBS
- Should override recorded playback
- Can currently be overridden by automation if not handled carefully

---

## Scripts Explained

### `start_stream.sh`
Starts a recorded stream.

- No safety checks
- Can cause duplicate streams

```bash
./start_stream.sh Andrea.mp4
```

---

### `start_stream_smart.sh` ✅
Preferred launcher.

- Detects existing streamer
- Prevents duplicate launches

```bash
./start_stream_smart.sh Andrea.mp4
```

---

### `stop_stream.sh`
Stops any active stream processes.

```bash
./stop_stream.sh
```

---

### `health_check.sh`
Cron-triggered watchdog.

- Runs every 2 minutes
- Restarts recorded stream if none is running
- **Does NOT distinguish live vs recorded mode**

Main cause of live-stream override issues.

---

## Cron Jobs

```cron
*/2 * * * * /root/health_check.sh
```

---

## Known Issue (Critical)

**Live streams can be overridden** because the health check assumes any stopped stream is a failure.

---

## Planned Fix

Introduce a stream state flag:

```text
/root/STREAM_MODE
```

Values:
- `live`
- `recorded`
- `idle`

Update `health_check.sh` to respect this state.

---

## Security Notes

- Never expose:
  - Server IP publicly
  - SSH credentials
  - Cron automation logic
- Keep this repo **private**

---

## TL;DR

- MP4 sets live in `/root`
- Use `start_stream_smart.sh`
- `health_check.sh` is aggressive
- Live override issue is architectural
