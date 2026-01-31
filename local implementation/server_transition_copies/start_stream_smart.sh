#!/usr/bin/env bash
set -Eeuo pipefail

# --- single-instance lock to prevent multiple publishers ---
exec 9>/var/run/stream.lock
flock -n 9 || { echo "Another streamer is running. Exiting."; exit 0; }

# ---------- Defaults & Preflight (one-command startup) ----------
: "${RTMP_URL:=rtmp://89.117.16.160/live/stream}"
: "${INPUT_CONCAT_FILE:=/root/input.txt}"
: "${PLAYLIST_FILE:=/root/playlist.txt}"
: "${DURATIONS_FILE:=/root/durations.txt}"
: "${STATE_FILE:=/root/stream_state.tsv}"
: "${FFMPEG_LOG:=/root/ffmpeg_random_stream.log}"
: "${FPS:=60}"
: "${GOP:=120}"
: "${VB:=3500k}"
: "${VBMAX:=3500k}"
: "${VBBUF:=7000k}"
: "${AB:=512k}"
: "${AR:=48000}"
: "${RECENCY_MAX_DUP:=5}"
: "${RECENCY_BIAS_DAYS:=14}"

# Ensure writable temp files exist
: > "$FFMPEG_LOG"
: > "$PLAYLIST_FILE" || true
: > "$DURATIONS_FILE" || true
: > /tmp/all_entries.txt || true
: > "$STATE_FILE" || true

# If the concat file is missing or empty, build a simple starter list from /root/*.mp4
if [[ ! -s "$INPUT_CONCAT_FILE" ]]; then
  mapfile -t _FILES < <(find /root -maxdepth 1 -type f -name "*.mp4" -printf "%p\n" | sort)
  if (( ${#_FILES[@]} == 0 )); then
    echo "❌ No mp4 files found in /root — cannot build playlist." | tee -a "$FFMPEG_LOG"
    exit 1
  fi
  printf "%s\n" "${_FILES[@]}" > "$PLAYLIST_FILE"
  awk '{print "file \x27"$0"\x27"}' "$PLAYLIST_FILE" > "$INPUT_CONCAT_FILE"
  echo "[bootstrap] Built starter concat at $INPUT_CONCAT_FILE with ${#_FILES[@]} files" | tee -a "$FFMPEG_LOG"
fi

# Ensure a row exists in the state file for a given media path
ensure_state_entry() {
  local f="$1"
  local now_ts
  now_ts=$(date +%s)
  # if no exact path entry exists, add: path|plays|first_seen|last_played
  grep -qF "^${f}|" "$STATE_FILE" || echo "${f}|0|${now_ts}|0" >> "$STATE_FILE"
}

# ---------- Playlist builder (atomic, long, state-updating) ----------
# Build a long weighted playlist and swap it in atomically. Also updates plays/last_played.
# MIN_PLAY_MINUTES ensures we don't hit EOF quickly.
: "${MIN_PLAY_MINUTES:=10080}"   # target total duration for a single playlist (~6h)

publish_playlist() {
  local now total_dur tmp_all tmp_pl tmp_concat tmp_durs min_secs
  now=$(date +%s)
  min_secs=$(( MIN_PLAY_MINUTES * 60 ))

  tmp_all="$(mktemp)"; tmp_pl="$(mktemp)"; tmp_concat="$(mktemp)"; tmp_durs="$(mktemp)"
  # cleanup temp files if function exits early
  trap 'rm -f "$tmp_all" "$tmp_pl" "$tmp_concat" "$tmp_durs"' RETURN

  # Re-scan files
  mapfile -t FILES < <(find /root -maxdepth 1 -type f -name "*.mp4" -printf "%p\n" | sort)
  [[ ${#FILES[@]} -eq 0 ]] && { echo "❌ No mp4 files found in /root" | tee -a "$FFMPEG_LOG"; return 1; }

  # Ensure state rows exist
  for f in "${FILES[@]}"; do ensure_state_entry "$f"; done

  # Compute average plays among *current* files (latest rows only)
  avg_plays=$(awk -F'|' '{m[$1]=$0} END{c=0;s=0; for (k in m){split(m[k],a,"|"); s+=a[2]; c++} if(c==0)print 0; else print int((s+c/2)/c)}' "$STATE_FILE")

  # Use associative array to count how many times we enqueue each file (to update plays later)
  declare -A COUNT
  total_dur=0

  for f in "${FILES[@]}"; do
    base="${f##*/}"
    is_loop=0; [[ "$base" == loop_* ]] && is_loop=1

    # duration (probe once per build)
    dur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null || true)"; dur="${dur%.*}"; [[ -z "$dur" ]] && dur=0
    echo "$f|$dur" >> "$tmp_durs"

    # load latest state
    IFS='|' read -r _ plays first_seen last_played < <(grep -F "${f}|" "$STATE_FILE" | tail -n1 || true)
    [[ -z "${plays:-}" ]] && plays=0
    [[ -z "${first_seen:-}" || "$first_seen" -le 0 ]] && first_seen="$now"

    # recency window
    age_days=$(( (now - first_seen + 86399) / 86400 ))
    if (( age_days <= 1 )); then rdup=${RECENCY_MAX_DUP:-5}
    elif (( age_days <= 3 )); then rdup=$(( (${RECENCY_MAX_DUP:-5} - 1) ))
    elif (( age_days <= 7 )); then rdup=$(( (${RECENCY_MAX_DUP:-5} - 2) ))
    elif (( age_days <= ${RECENCY_BIAS_DAYS:-14} )); then rdup=2
    else rdup=1
    fi
    (( rdup < 1 )) && rdup=1

    # fairness vs avg
    diff=$(( avg_plays - plays ))
    if (( diff <= 0 )); then fdup=1; else (( diff>3 )) && diff=3; fdup=$((1+diff)); fi

    # combine, clamp
    dup=$(( rdup + fdup - 1 ))
    (( dup < 1 )) && dup=1
    (( dup > (${RECENCY_MAX_DUP:-5} + 3) )) && dup=$(( (${RECENCY_MAX_DUP:-5} + 3) ))
    (( is_loop == 1 )) && dup=1

    # enqueue
    for ((i=0;i<dup;i++)); do
      echo "$f" >> "$tmp_all"
      COUNT["$f"]=$(( ${COUNT["$f"]:-0} + 1 ))
      total_dur=$(( total_dur + dur ))
    done

    echo "[weight] $f age=${age_days}d plays=${plays} avg=${avg_plays} dup=$dup (recency=$rdup fairness=$fdup)" >> "$FFMPEG_LOG"
  done

  # fallback if somehow empty
  if [[ ! -s "$tmp_all" ]]; then
    printf "%s\n" "${FILES[@]}" > "$tmp_all"
    # pessimistic duration sum
    for f in "${FILES[@]}"; do d=$(awk -F'|' -v k="$f" '$1==k{print $2}' "$tmp_durs"); total_dur=$(( total_dur + ${d:-0} )); done
    echo "[weight-fallback] empty weighting — using 1x each file" >> "$FFMPEG_LOG"
  fi

  # If total duration is below target, replicate shuffled chunks until target met (cap to avoid huge files)
  shuf "$tmp_all" > "$tmp_pl"
  while (( total_dur < min_secs )); do
    cat "$tmp_pl" >> "$tmp_all"
    # recompute duration by summing durations of appended block
    while IFS= read -r line; do d=$(awk -F'|' -v k="$line" '$1==k{print $2}' "$tmp_durs"); total_dur=$(( total_dur + ${d:-0} )); COUNT["$line"]=$(( ${COUNT["$line"]:-0} + 1 )); done < "$tmp_pl"
    # guard: stop at ~24h to avoid runaway
    (( total_dur > 86400 )) && break
  done

  # Final shuffle and publish
  shuf "$tmp_all" > "$tmp_pl"
  awk '{print "file \x27"$0"\x27"}' "$tmp_pl" > "$tmp_concat"

  # Atomic swaps: only now replace live files
  mv -f "$tmp_pl" "$PLAYLIST_FILE"
  mv -f "$tmp_concat" "$INPUT_CONCAT_FILE"
  mv -f "$tmp_durs" "$DURATIONS_FILE"

  # Append state updates (tail -n1 will read newest row later)
  for f in "${!COUNT[@]}"; do
    IFS='|' read -r _ plays first_seen last_played < <(grep -F "${f}|" "$STATE_FILE" | tail -n1 || true)
    plays=$(( ${plays:-0} + ${COUNT["$f"]} ))
    [[ -z "${first_seen:-}" || "$first_seen" -le 0 ]] && first_seen="$now"
    echo "${f}|${plays}|${first_seen}|${now}" >> "$STATE_FILE"
  done

  echo "[playlist] published $(wc -l < "$PLAYLIST_FILE") entries, ~$(( total_dur/60 )) min" | tee -a "$FFMPEG_LOG"
}

# ---------- Run ffmpeg with stable encoding ----------
# NOTE: We keep a single ffmpeg process over the whole shuffled list to avoid RTMP reconnects.
# If you want endless play, we relaunch on exit and reshuffle.

run_ffmpeg() {
  ffmpeg -hide_banner -loglevel info -re -fflags +genpts+igndts \
    -f concat -safe 0 -i "$INPUT_CONCAT_FILE" \
    -map 0:v:0 -map 0:a:0? \
    -vf "setpts=N/${FPS}/TB,format=yuv420p" \
    -c:v libx264 -preset veryfast -pix_fmt yuv420p -r "$FPS" \
    -g "$GOP" -keyint_min "$GOP" -sc_threshold 0 \
    -x264-params "scenecut=0:keyint=$GOP:min-keyint=$GOP:open_gop=0" \
    -force_key_frames "expr:gte(t,n_forced*2)" \
    -b:v "$VB" -maxrate "$VBMAX" -bufsize "$VBBUF" \
    -c:a aac -profile:a aac_low -b:a "$AB" -ar "$AR" -ac 2 \
    -af "aresample=resampler=soxr:osf=s32:dither_method=triangular_hp,asetpts=N/SR/TB" \
    -vsync 1 -muxpreload 0 -muxdelay 0 \
    -f flv "$RTMP_URL"
}

echo "🚀 Starting FFmpeg…"

# Build an initial long playlist before launching ffmpeg
publish_playlist || { echo "❌ Failed to build initial playlist" | tee -a "$FFMPEG_LOG"; exit 1; }

# Main supervise loop: run ffmpeg; when it exits, rebuild and restart
while true; do
  set +e
  run_ffmpeg 2>&1 | tee -a "$FFMPEG_LOG"
  rc=${PIPESTATUS[0]}
  set -e
  echo "[FFMPEG_EXIT $(date -Is)] rc=$rc — rebuilding and restarting in 2s…" | tee -a "$FFMPEG_LOG"
  sleep 2
  publish_playlist || echo "⚠️ rebuild failed — keeping previous playlist" | tee -a "$FFMPEG_LOG"
  # small backoff if ffmpeg crashed too fast
  [[ ${rc:-0} -ne 0 ]] && sleep 3
done
