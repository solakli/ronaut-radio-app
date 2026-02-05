#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${MOVIES_DIR:=/Users/andreabenedetti/Movies}"
: "${RTMP_PORT:=1935}"
: "${CLIP_SECONDS:=30}"
: "${START_OFFSET:=0}"
: "${NOW_PLAYING_FILE:=${BASE_DIR}/run/now_playing.txt}"
: "${RTMP_URL:=rtmp://localhost:${RTMP_PORT}/live/stream}"
: "${FFMPEG_BIN:=/Users/andreabenedetti/opt/anaconda3/envs/ronaut-ffmpeg/bin/ffmpeg}"
: "${LOCK_FILE:=}"
: "${STREAM_MODE_FILE:=${BASE_DIR}/run/STREAM_MODE}"

: "${FPS:=60}"
: "${GOP:=120}"
: "${VB:=3500k}"
: "${VBMAX:=3500k}"
: "${VBBUF:=7000k}"
: "${AB:=512k}"
: "${AR:=48000}"
: "${AUDIO_FILTER:=}"
: "${VIDEO_FILTER:=format=yuv420p}"

mkdir -p "$(dirname "$NOW_PLAYING_FILE")"

if [[ -n "${LOCK_FILE}" ]]; then
  mkdir -p "$(dirname "$LOCK_FILE")"
  exec 9>"$LOCK_FILE"
  if ! command -v flock >/dev/null 2>&1; then
    echo "flock not available; lock file is not enforced." >&2
  elif ! flock -n 9; then
    echo "Another streamer is running (lock: $LOCK_FILE). Exiting." >&2
    exit 0
  fi
fi

if [[ ! -d "$MOVIES_DIR" ]]; then
  echo "Movies folder not found: $MOVIES_DIR"
  exit 1
fi

FILES=()
if [[ ${#@} -gt 0 ]]; then
  TARGET="$1"
  if [[ -f "$TARGET" ]]; then
    FILES+=("$TARGET")
  elif [[ -f "${MOVIES_DIR}/${TARGET}" ]]; then
    FILES+=("${MOVIES_DIR}/${TARGET}")
  else
    echo "Target file not found: $TARGET"
    exit 1
  fi
else
  while IFS= read -r f; do
    FILES+=("$f")
  done < <(find "$MOVIES_DIR" -type f -name "*.mp4" -print | sort)
fi
if (( ${#FILES[@]} == 0 )); then
  echo "No MP4 files found under: $MOVIES_DIR"
  exit 1
fi

echo "Streaming ${#FILES[@]} files from $MOVIES_DIR"
echo "RTMP: $RTMP_URL"
echo "Clip length: ${CLIP_SECONDS}s (offset ${START_OFFSET}s)"
if [[ -z "$RTMP_URL" ]]; then
  echo "RTMP_URL is empty; cannot start streaming." >&2
  exit 1
fi

get_stream_mode() {
  if [[ -f "$STREAM_MODE_FILE" ]]; then
    tr -d '[:space:]' < "$STREAM_MODE_FILE"
  else
    echo "recorded"
  fi
}

while true; do
  mode="$(get_stream_mode)"
  if [[ "$mode" == "live" ]]; then
    echo "[mode=live] Recorded playback paused."
    sleep 5
    continue
  fi
  for FILE in "${FILES[@]}"; do
    BASENAME="${FILE##*/}"
    DISPLAY="${BASENAME%.mp4}"

    echo "$DISPLAY" > "$NOW_PLAYING_FILE"
    echo "[now playing] $DISPLAY"

    unset AF_ARGS
    if [[ -n "${AUDIO_FILTER}" ]]; then
      AF_ARGS=(-af "${AUDIO_FILTER}")
    fi

    "$FFMPEG_BIN" -hide_banner -loglevel info -re -fflags +genpts+igndts \
      -ss "$START_OFFSET" -t "$CLIP_SECONDS" -i "$FILE" \
      -map 0:v:0 -map 0:a:0? \
      -vf "$VIDEO_FILTER" \
      -c:v libx264 -preset veryfast -pix_fmt yuv420p -r "$FPS" \
      -g "$GOP" -keyint_min "$GOP" -sc_threshold 0 \
      -x264-params "scenecut=0:keyint=$GOP:min-keyint=$GOP:open_gop=0" \
      -force_key_frames "expr:gte(t,n_forced*2)" \
      -b:v "$VB" -maxrate "$VBMAX" -bufsize "$VBBUF" \
      -c:a aac -profile:a aac_low -b:a "$AB" -ar "$AR" -ac 2 \
      ${AF_ARGS+"${AF_ARGS[@]}"} \
      -vsync 1 -muxpreload 0 -muxdelay 0 \
      -f flv "$RTMP_URL"
  done
done
