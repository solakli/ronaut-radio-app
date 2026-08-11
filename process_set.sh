#!/bin/bash
# Full post-add pipeline for a new set:
#   track ID (Shazam) → Discogs enrich → wax stats → wantlist sync → SoundCloud (private)
# Called by add_set.sh in the background; can also be run standalone/re-run safely.
# Usage: ./process_set.sh "Ronaut[026]-NewSet.mp4"

MP4_FILE="$1"
if [[ -z "$MP4_FILE" ]]; then
    echo "Usage: ./process_set.sh <filename.mp4>"
    exit 1
fi

APP="/root/ronaut-radio-app"
STEM="${MP4_FILE%.mp4}"
SAFE=$(echo "$STEM" | sed -E 's/[^A-Za-z0-9._-]+/_/g')

# Same normalization as the API / add_set.sh
NORM=$(echo "$STEM" | sed -E 's/^[Rr]onaut\[[0-9]+\]-?//' | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')
TRACKLIST="/root/tracklists/${NORM}.json"
LOG="/root/process_${SAFE}.log"

exec >> "$LOG" 2>&1
echo ""
echo "########## $(date -Is) process_set.sh $MP4_FILE ##########"

if [[ ! -f "/root/$MP4_FILE" ]]; then
    echo "ERROR: /root/$MP4_FILE not found"
    exit 1
fi

echo "=== [1/5] Track identification (Shazam) → $TRACKLIST"
if [[ -s "$TRACKLIST" ]]; then
    echo "  tracklist already exists, skipping ID (delete it to re-run)"
else
    python3 "$APP/track_identifier.py" --shazam "/root/$MP4_FILE" "$TRACKLIST"
fi

echo "=== [2/5] Discogs enrichment"
python3 "$APP/enrich_discogs.py" "$TRACKLIST"

echo "=== [3/5] Marketplace stats (wax)"
python3 "$APP/wax_stats.py" "$TRACKLIST"

echo "=== [4/5] Wantlist sync"
python3 "$APP/wantlist_sync.py"

echo "=== [5/5] SoundCloud prep + upload (private)"
python3 "$APP/sc_prep.py" "$MP4_FILE"
python3 "$APP/sc_upload.py" "$SAFE"

echo "########## PIPELINE DONE $(date -Is) ##########"
echo "Review the private SoundCloud link above, then publish with:"
echo "  python3 $APP/sc_upload.py --publish-all   (or flip it in the SoundCloud UI)"
