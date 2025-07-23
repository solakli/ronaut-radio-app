#!/usr/bin/env bash
# stream-start: plays every .mp4 in /root using ffmpeg → nginx RTMP,
# updates /root/now_playing.txt for the Flask API, and loops forever.

set -e

PLAYLIST="/root/playlist.txt"
NOW_PLAY="/root/now_playing.txt"
RTMP_URL="rtmp://localhost/live/stream"

cd /root || exit 1

# Build playlist.txt from all mp4 files once at boot
rm -f "$PLAYLIST"
for f in *.mp4; do
  [[ -e "$f" ]] || continue
  echo "$f" >> "$PLAYLIST"
done

mapfile -t FILES < "$PLAYLIST"
[[ ${#FILES[@]} -eq 0 ]] && { echo "❌ No mp4 files found in /root"; exit 1; }

# Pick random starting index
START=$((RANDOM % ${#FILES[@]}))
echo "▶️  Starting at index $START (${FILES[$START]})"

while true; do
  for ((i=0; i<${#FILES[@]}; i++)); do
    idx=$(((START + i) % ${#FILES[@]}))
    FILE="${FILES[$idx]}"
    BASENAME="${FILE##*/}"
    DISPLAY="${BASENAME%.mp4}"

    # Write to now_playing.txt for Flask
    echo "$DISPLAY" > "$NOW_PLAY"
    echo "🕒 Now playing: $DISPLAY"

    # Duration in whole seconds
    DUR=$(ffprobe -v quiet -show_entries format=duration \
          -of default=noprint_wrappers=1:nokey=1 "$FILE")
    DUR=${DUR%.*}

    # Launch ffmpeg in background
    ffmpeg -re -i "$FILE" -c copy -f flv "$RTMP_URL" &
    FPID=$!

    # Sleep for duration + 3 s buffer
    sleep $((DUR + 3))
    kill "$FPID" 2>/dev/null || true
  done
done