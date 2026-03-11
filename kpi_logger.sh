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
    segment_age=$(awk "BEGIN {printf \"%d\", $now - $newest_ts}")
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

# --- System CPU % (1-second /proc/stat sample — accurate, multi-core aware) ---
cpu1=($(awk '/^cpu /{print $2,$3,$4,$5,$6,$7,$8}' /proc/stat))
sleep 1
cpu2=($(awk '/^cpu /{print $2,$3,$4,$5,$6,$7,$8}' /proc/stat))
total1=0; for v in "${cpu1[@]}"; do total1=$((total1+v)); done
total2=0; for v in "${cpu2[@]}"; do total2=$((total2+v)); done
idle1=${cpu1[3]}; idle2=${cpu2[3]}
dtotal=$((total2-total1)); didle=$((idle2-idle1))
ffmpeg_cpu=$(awk "BEGIN{printf \"%.1f\", 100*($dtotal-$didle)/$dtotal}")
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
freeze_events=$(grep -c "$TODAY.*frozen" "$HEALTH_LOG" 2>/dev/null; true)
freeze_events=${freeze_events:-0}
restart_events=$(grep -c "$TODAY.*Restart triggered\|$TODAY.*Supervisor started" "$HEALTH_LOG" 2>/dev/null; true)
restart_events=${restart_events:-0}

# --- A/V timestamp drift (audio PTS minus video PTS in newest live segment) ---
# This predicts startup delay: ~0s = fast, ~2s = 8s delay, ~4s = 20s delay
newest_ts_file=$(find "$HLS_DIR" -name "stream-*.ts" -printf "%T@\t%p\n" 2>/dev/null | sort -n | tail -1 | cut -f2)
if [[ -n "$newest_ts_file" ]]; then
    av_drift=$(ffprobe -v quiet -print_format json -show_packets \
        -read_intervals "%+#20" "$newest_ts_file" 2>/dev/null | \
        python3 -c "
import json, sys
d = json.load(sys.stdin)
a = [float(p['pts_time']) for p in d['packets'] if p.get('codec_type')=='audio' and 'pts_time' in p]
v = [float(p['pts_time']) for p in d['packets'] if p.get('codec_type')=='video' and 'pts_time' in p]
print(f'{abs(a[0]-v[0]):.3f}' if a and v else -1)
" 2>/dev/null || echo -1)
else
    av_drift=-1
fi
av_drift=${av_drift:-"-1"}

# --- Write TSV header if file is new ---
if [[ ! -f "$KPI_LOG" ]]; then
    printf "timestamp\tstatus\tsegment_age_s\tsegment_count\tffmpeg_speed\tffmpeg_fps\tffmpeg_bitrate_kbps\tffmpeg_cpu_pct\tffmpeg_uptime_min\tram_free_gb\tlive_listeners\tfreeze_today\trestarts_today\tav_drift_s\n" \
        > "$KPI_LOG"
fi

# --- Append KPI snapshot ---
printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$TIMESTAMP" "$status" "$segment_age" "$segment_count" \
    "$ffmpeg_speed" "$ffmpeg_fps" "$ffmpeg_bitrate" "$ffmpeg_cpu" \
    "$ffmpeg_uptime_min" "$ram_free" "$live_listeners" \
    "$freeze_events" "$restart_events" "$av_drift" \
    >> "$KPI_LOG"
