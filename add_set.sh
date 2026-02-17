#!/bin/bash
# Add a new set to Staff Picks with HLS conversion and thumbnail generation
# Usage: ./add_set.sh "Ronaut[015]-NewSet.mp4" "Set Title" "Description here"

set -e

MP4_FILE="$1"
TITLE="$2"
DESCRIPTION="${3:-}"

if [[ -z "$MP4_FILE" || -z "$TITLE" ]]; then
    echo "Usage: ./add_set.sh <filename.mp4> <title> [description]"
    echo "Example: ./add_set.sh 'Ronaut[015]-NewSet.mp4' 'New Set' 'Sunday vibes'"
    exit 1
fi

MP4_PATH="/root/$MP4_FILE"
STAFF_PICKS="/root/staff_picks.json"
HLS_DIR="/var/www/html/hls-vod"
THUMB_DIR="/root/ronaut-radio-app/sets/thumbs"

# Check MP4 exists
if [[ ! -f "$MP4_PATH" ]]; then
    echo "ERROR: $MP4_PATH not found"
    exit 1
fi

# Normalize name for HLS (lowercase, no punctuation, no prefix)
normalize_name() {
    local name="$1"
    name="${name%.mp4}"
    # Remove Ronaut[XXX]- prefix
    name=$(echo "$name" | sed -E 's/^[Rr]onaut\[[0-9]+\]-?//')
    # Remove non-alphanumeric, lowercase
    name=$(echo "$name" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')
    echo "$name"
}

HLS_NAME=$(normalize_name "$MP4_FILE")
THUMB_NAME="${MP4_FILE%.mp4}"

echo "=== Adding Set: $TITLE ==="
echo "MP4: $MP4_PATH"
echo "HLS name: $HLS_NAME"
echo "Thumbnail: $THUMB_NAME.jpg"
echo ""

# Step 1: Generate HLS
echo "[1/4] Generating HLS..."
ffmpeg -y -i "$MP4_PATH" -c copy -hls_time 10 -hls_list_size 0 \
    -hls_segment_filename "$HLS_DIR/${HLS_NAME}_%04d.ts" \
    "$HLS_DIR/${HLS_NAME}.m3u8"

if grep -q "EXT-X-ENDLIST" "$HLS_DIR/${HLS_NAME}.m3u8"; then
    echo "  HLS verified OK"
else
    echo "  WARNING: HLS may be incomplete"
fi

# Step 2: Generate thumbnail (at 5 minutes or 10% if shorter)
echo "[2/4] Generating thumbnail..."
DURATION=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$MP4_PATH" | cut -d. -f1)
THUMB_TIME="00:05:00"
if [[ "$DURATION" -lt 300 ]]; then
    THUMB_SEC=$((DURATION / 10))
    THUMB_TIME=$(printf "%02d:%02d:%02d" $((THUMB_SEC/3600)) $((THUMB_SEC%3600/60)) $((THUMB_SEC%60)))
fi
ffmpeg -y -i "$MP4_PATH" -ss "$THUMB_TIME" -vframes 1 -q:v 2 "$THUMB_DIR/$THUMB_NAME.jpg"
echo "  Thumbnail created at $THUMB_TIME"

# Step 3: Add to staff_picks.json
echo "[3/4] Adding to staff_picks.json..."
# Create backup
cp "$STAFF_PICKS" "${STAFF_PICKS}.bak"

# Add new entry using Python (safer JSON handling)
python3 << PYEOF
import json

with open("$STAFF_PICKS", "r") as f:
    picks = json.load(f)

new_entry = {
    "filename": "$MP4_FILE",
    "title": "$TITLE",
    "description": "$DESCRIPTION"
}

# Check if already exists
exists = any(p.get("filename") == "$MP4_FILE" for p in picks)
if exists:
    print("  Already in staff_picks.json, skipping")
else:
    picks.append(new_entry)
    with open("$STAFF_PICKS", "w") as f:
        json.dump(picks, f, indent=2)
    print("  Added to staff_picks.json")
PYEOF

# Step 4: Verify
echo "[4/4] Verifying..."
echo "  HLS: $HLS_DIR/${HLS_NAME}.m3u8 ($(ls -lh "$HLS_DIR/${HLS_NAME}.m3u8" | awk '{print $5}'))"
echo "  Thumb: $THUMB_DIR/$THUMB_NAME.jpg ($(ls -lh "$THUMB_DIR/$THUMB_NAME.jpg" | awk '{print $5}'))"
echo "  Duration: ${DURATION}s"

echo ""
echo "=== DONE ==="
echo "Set '$TITLE' added to Staff Picks"
echo "It will appear on the website immediately (API reads dynamically)"
