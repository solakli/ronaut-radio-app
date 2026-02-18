#!/usr/bin/env bash
# Ronaut Radio stream health check
# Runs every 2 min via cron. Sends Discord alert + restarts if stream is truly down.

WEBHOOK="https://discord.com/api/webhooks/1472307215892353258/yOZTSpu7DfnYEOCuXlkSZxk0Vsbn8SXH5a7CZbcHh97OVCAn70XCZCQ8d3kjee8c5ltq"
STREAM_SCRIPT="/root/ronaut-radio-app/start_stream_smart.sh"
LOG="/root/health_check.log"

# If live event is active (OBS streaming), do NOT interfere
[[ -f /root/live_mode.flag ]] && exit 0

# If ffmpeg is streaming, all good
pgrep -f "ffmpeg.*live/stream" >/dev/null && exit 0

# If stream script is already running (starting up), all good
pgrep -f "start_stream_smart.sh" >/dev/null && exit 0

# Stream is truly down — wait 10s to rule out brief glitch
sleep 10
pgrep -f "ffmpeg.*live/stream" >/dev/null && exit 0
pgrep -f "start_stream_smart.sh" >/dev/null && exit 0

# Confirmed down — send Discord alert
echo "[$(date -Is)] Stream down, restarting..." >> "$LOG"
curl -s -X POST "$WEBHOOK" \
  -H "Content-Type: application/json" \
  -d '{
    "embeds": [{
      "title": "⚠️ Stream Offline",
      "description": "Ronaut Radio stream went offline. Auto-restarting now...",
      "color": 15158332
    }]
  }' >/dev/null

# Restart stream
nohup bash "$STREAM_SCRIPT" > /root/stream.log 2>&1 &
echo "[$(date -Is)] Restart triggered (PID $!)" >> "$LOG"
