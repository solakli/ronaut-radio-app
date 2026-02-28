#!/usr/bin/env bash
# Ronaut Radio — Stream Health KPI Logger
# Runs every 5 minutes via cron. Appends one TSV row to /root/kpi.log
# Tracks: stream status, segment age, ffmpeg speed/fps/bitrate/cpu/uptime,
#         RAM, live listeners, freeze/restart events

HLS_DIR="/var/www/html/hls"
FFMPEG_LOG="/root/ffmpeg_random_stream.log"
HEALTH_LOG="/root/health_check.log"
NGINX_ACCESS="/var/log/nginx/access.log"
KPI_LOG="/root/kpi.log"

TIMESTAMP=$(date -Iseconds)
TODAY=$(date +%Y-%m-%d)

# --- HLS segment age (seconds since newest .ts was written) ---
# This is the most critical metric — >30s means stream is frozen
newest_ts=$(find "$HLS_DIR" -name "*.ts" -printf "%T@\n" 2>/dev/null | sort -n | tail -1)
if [[ -n "$newest_ts" ]]; then
    now=$(date +%s)
    segment_age=$(echo "$now - $newest_ts" | bc | cut -d. -f1)
else
    segment_age=9999
fi

# --- HLS segment count (healthy = ~15 segments for 60s playlist at 4s each) ---
segment_count=$(find "$HLS_DIR" -name "*.ts" 2>/dev/null | wc -l)

# --- Stream status ---
if [[ $segment_age -lt 15 ]]; then
    status="healthy"
elif [[ $segment_age -lt 30 ]]; then
    status="degraded"
else
    status="frozen"
fi

# --- ffmpeg metrics (parse last progress line from log) ---
# ffmpeg writes progress with \r so we read the tail and split on carriage returns
last_progress=$(tail -c 4096 "$FFMPEG_LOG" 2>/dev/null | tr '\r' '\n' | grep -oP "fps=.*speed=\K[^\s]+" | tail -1 || true)
ffmpeg_speed=$(tail -c 4096 "$FFMPEG_LOG" 2>/dev/null | tr '\r' '\n' | grep -oP "speed=\K[0-9.]+" | tail -1)
ffmpeg_fps=$(tail -c 4096 "$FFMPEG_LOG" 2>/dev/null | tr '\r' '\n' | grep -oP "fps=\s*\K[0-9]+" | tail -1)
ffmpeg_bitrate=$(tail -c 4096 "$FFMPEG_LOG" 2>/dev/null | tr '\r' '\n' | grep -oP "bitrate=\s*\K[0-9.]+" | tail -1)
ffmpeg_speed=${ffmpeg_speed:-"0"}
ffmpeg_fps=${ffmpeg_fps:-"0"}
ffmpeg_bitrate=${ffmpeg_bitrate:-"0"}

# --- ffmpeg CPU % ---
ffmpeg_cpu=$(ps aux | grep "ffmpeg.*live/stream" | grep -v grep | awk '{print $3}' | head -1)
ffmpeg_cpu=${ffmpeg_cpu:-"0"}

# --- ffmpeg process uptime (minutes) ---
ffmpeg_pid=$(pgrep -f "ffmpeg.*live/stream" | head -1)
if [[ -n "$ffmpeg_pid" ]]; then
    start_epoch=$(stat -c %Y /proc/"$ffmpeg_pid" 2>/dev/null || echo "$(date +%s)")
    ffmpeg_uptime_min=$(( ($(date +%s) - start_epoch) / 60 ))
else
    ffmpeg_uptime_min=0
fi

# --- RAM free (GB) ---
ram_free=$(free -g | awk '/^Mem:/{print $7}')

# --- Live HLS listener count ---
# Count unique IPs that requested live .ts segments in the last ~5 min
# (tail last 3000 lines ≈ ~5 min at moderate load — fast and accurate enough)
live_listeners=$(tail -3000 "$NGINX_ACCESS" 2>/dev/null \
    | grep -oP '^\S+(?=.*live/hls/stream-[0-9]+\.ts)' \
    | sort -u | wc -l)
live_listeners=${live_listeners:-0}

# --- Freeze and restart events today (from health_check.log) ---
freeze_events=$(grep -c "$TODAY.*frozen" "$HEALTH_LOG" 2>/dev/null || echo 0)
restart_events=$(grep -c "$TODAY.*Restart triggered\|$TODAY.*Supervisor started" "$HEALTH_LOG" 2>/dev/null || echo 0)

# --- Write TSV header if file is new ---
if [[ ! -f "$KPI_LOG" ]]; then
    printf "timestamp\tstatus\tsegment_age_s\tsegment_count\tffmpeg_speed\tffmpeg_fps\tffmpeg_bitrate_kbps\tffmpeg_cpu_pct\tffmpeg_uptime_min\tram_free_gb\tlive_listeners\tfreeze_today\trestarts_today\n" \
        > "$KPI_LOG"
fi

# --- Append KPI snapshot ---
printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$TIMESTAMP" "$status" "$segment_age" "$segment_count" \
    "$ffmpeg_speed" "$ffmpeg_fps" "$ffmpeg_bitrate" "$ffmpeg_cpu" \
    "$ffmpeg_uptime_min" "$ram_free" "$live_listeners" \
    "$freeze_events" "$restart_events" \
    >> "$KPI_LOG"
