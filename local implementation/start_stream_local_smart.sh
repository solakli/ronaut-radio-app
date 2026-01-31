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

: "${FPS:=30}"
: "${GOP:=60}"
: "${VB:=2500k}"
: "${VBBUF:=5000k}"
: "${AB:=192k}"
: "${AR:=44100}"

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
while IFS= read -r f; do
  FILES+=("$f")
done < <(find "$MOVIES_DIR" -type f -name "*.mp4" -print | sort)
if (( ${#FILES[@]} == 0 )); then
  echo "No MP4 files found under: $MOVIES_DIR"
  exit 1
fi

echo "Streaming ${#FILES[@]} files from $MOVIES_DIR"
echo "RTMP: $RTMP_URL"
echo "Clip length: ${CLIP_SECONDS}s (offset ${START_OFFSET}s)"

while true; do
  for FILE in "${FILES[@]}"; do
    BASENAME="${FILE##*/}"
    DISPLAY="${BASENAME%.mp4}"

    echo "$DISPLAY" > "$NOW_PLAYING_FILE"
    echo "[now playing] $DISPLAY"

    "$FFMPEG_BIN" -hide_banner -loglevel info -re \
      -ss "$START_OFFSET" -t "$CLIP_SECONDS" -i "$FILE" \
      -map 0:v:0 -map 0:a:0? \
      -vf "format=yuv420p" \
      -c:v libx264 -preset veryfast -pix_fmt yuv420p -r "$FPS" \
      -g "$GOP" -keyint_min "$GOP" -sc_threshold 0 \
      -b:v "$VB" -maxrate "$VB" -bufsize "$VBBUF" \
      -c:a aac -b:a "$AB" -ar "$AR" -ac 2 \
      -f flv "$RTMP_URL"
  done
done
