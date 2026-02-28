#!/usr/bin/env bash
# Ronaut Radio stream health check
# Runs every 2 min via cron. Sends Discord alert + restarts if stream is truly down or frozen.

WEBHOOK="https://discord.com/api/webhooks/1472307215892353258/yOZTSpu7DfnYEOCuXlkSZxk0Vsbn8SXH5a7CZbcHh97OVCAn70XCZCQ8d3kjee8c5ltq"
STREAM_SCRIPT="/root/ronaut-radio-app/start_stream_smart.sh"
HLS_DIR="/var/www/html/hls"
LOG="/root/health_check.log"
MAX_SEGMENT_AGE=30  # seconds — if newest .ts is older than this, stream is frozen

# If live event is active (OBS streaming), do NOT interfere
[[ -f /root/.live_mode ]] && exit 0

# If supervisor is starting up, leave it alone
pgrep -f "start_stream_smart.sh" >/dev/null && exit 0

ffmpeg_pid=$(pgrep -f "ffmpeg.*live/stream" | head -1)
supervisor_running=0
pgrep -f "start_stream_smart.sh" >/dev/null && supervisor_running=1

# Check if HLS segments are fresh (catches frozen ffmpeg)
is_frozen=0
if [[ -n "$ffmpeg_pid" ]]; then
  newest_ts=$(find "$HLS_DIR" -name "*.ts" -printf "%T@\n" 2>/dev/null | sort -n | tail -1)
  if [[ -z "$newest_ts" ]]; then
    is_frozen=1
  else
    now=$(date +%s)
    age=$(echo "$now - $newest_ts" | bc | cut -d. -f1)
    [[ $age -gt $MAX_SEGMENT_AGE ]] && is_frozen=1
  fi
fi

# All good
[[ -n "$ffmpeg_pid" && $is_frozen -eq 0 ]] && exit 0

# Wait 10s to rule out a brief transition glitch, then recheck
sleep 10
pgrep -f "start_stream_smart.sh" >/dev/null && exit 0

ffmpeg_pid=$(pgrep -f "ffmpeg.*live/stream" | head -1)

is_frozen=0
if [[ -n "$ffmpeg_pid" ]]; then
  newest_ts=$(find "$HLS_DIR" -name "*.ts" -printf "%T@\n" 2>/dev/null | sort -n | tail -1)
  if [[ -z "$newest_ts" ]]; then
    is_frozen=1
  else
    now=$(date +%s)
    age=$(echo "$now - $newest_ts" | bc | cut -d. -f1)
    [[ $age -gt $MAX_SEGMENT_AGE ]] && is_frozen=1
  fi
fi

[[ -n "$ffmpeg_pid" && $is_frozen -eq 0 ]] && exit 0

# --- Something is wrong ---

if [[ -n "$ffmpeg_pid" && $is_frozen -eq 1 ]]; then
  # ffmpeg is running but frozen — kill it only.
  # The supervisor (while true loop in start_stream_smart.sh) will detect the death
  # and restart ffmpeg automatically with the same playlist. No reshuffle needed.
  echo "[$(date -Is)] ffmpeg frozen (no HLS segments in ${MAX_SEGMENT_AGE}s) — killing PID $ffmpeg_pid, supervisor will restart" >> "$LOG"
  kill "$ffmpeg_pid" 2>/dev/null || true

  curl -s -X POST "$WEBHOOK" \
    -H "Content-Type: application/json" \
    -d '{"embeds":[{"title":"⚠️ Stream Frozen","description":"ffmpeg frozen — killed and restarting via supervisor. No playlist change.","color":15158332}]}' >/dev/null

elif [[ -z "$ffmpeg_pid" ]]; then
  # ffmpeg is completely gone (and supervisor too, otherwise we'd have exited early)
  echo "[$(date -Is)] Stream fully down — starting supervisor from scratch" >> "$LOG"

  nohup bash "$STREAM_SCRIPT" >> /root/ffmpeg_random_stream.log 2>&1 &
  echo "[$(date -Is)] Supervisor started (PID $!)" >> "$LOG"

  curl -s -X POST "$WEBHOOK" \
    -H "Content-Type: application/json" \
    -d '{"embeds":[{"title":"⚠️ Stream Offline","description":"Stream was fully down. Supervisor restarted.","color":15158332}]}' >/dev/null
fi
