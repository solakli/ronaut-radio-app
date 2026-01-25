

#!/usr/bin/env bash

set -e
cd "$(dirname "$0")" || exit 1

echo "🔍 Deep-scanning and validating MP4 files in $(pwd)..."

for FILE in *.mp4; do
  echo -e "\n🎬 Checking: $FILE"

  # Gather video and audio stream details
  V_CODEC=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=nw=1:nk=1 "$FILE")
  A_CODEC=$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of default=nw=1:nk=1 "$FILE")
  BITRATE=$(ffprobe -v error -select_streams a:0 -show_entries stream=bit_rate -of default=nw=1:nk=1 "$FILE")
  PROFILE=$(ffprobe -v error -select_streams v:0 -show_entries stream=profile -of default=nw=1:nk=1 "$FILE")
  LEVEL=$(ffprobe -v error -select_streams v:0 -show_entries stream=level -of default=nw=1:nk=1 "$FILE")
  R_FPS=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=nw=1:nk=1 "$FILE")
  A_FPS=$(ffprobe -v error -select_streams v:0 -show_entries stream=avg_frame_rate -of default=nw=1:nk=1 "$FILE")
  REFS=$(ffprobe -v error -select_streams v:0 -show_entries stream=refs -of default=nw=1:nk=1 "$FILE")

  # Print details
  echo "  🎥 Video codec:      $V_CODEC"
  echo "  🎚  Profile:           $PROFILE"
  echo "  🔢 Level:             $LEVEL"
  echo "  🔁 Ref frames:        $REFS"
  echo "  ⏱  r_frame_rate:      $R_FPS"
  echo "  📊 avg_frame_rate:    $A_FPS"
  echo "  🔊 Audio codec:       $A_CODEC"
  if [[ -n "$BITRATE" ]]; then
    echo "  🎚  Audio bitrate:     $((BITRATE / 1000)) kbps"
  else
    echo "  🎚  Audio bitrate:     (unknown)"
  fi

  # Check for compliance
  if [[ "$V_CODEC" == "h264" && "$A_CODEC" == "aac" && $BITRATE -ge 320000 ]]; then
    echo "✅ $FILE is compliant. Skipping conversion."
    continue
  fi

  echo "⚙️  Re-encoding $FILE → temp_$FILE"
  ffmpeg -y -i "$FILE" \
    -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p \
    -c:a aac -b:a 320k -ar 48000 -ac 2 \
    -movflags +faststart \
    "temp_$FILE"

  mv "temp_$FILE" "$FILE"
  echo "✅ Replaced original with re-encoded version: $FILE"
done

echo -e "\n🎉 Done validating and converting."