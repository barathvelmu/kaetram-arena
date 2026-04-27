#!/usr/bin/env bash

# ── --help / -h guard (auto-injected) ────────────────────────────────────────
for _arg in "$@"; do
  case "$_arg" in
    -h|--help)
      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
      exit 0
      ;;
  esac
done

# Extract last N seconds from a session recording
# Usage: cut-highlight.sh <input.webm> [duration=30] [output.mp4]
INPUT="$1"
DURATION="${2:-30}"
OUTPUT="${3:-$(dirname "$INPUT")/highlight_$(date +%s).mp4}"

TOTAL=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$INPUT")
START=$(echo "$TOTAL - $DURATION" | bc)
[ "$(echo "$START < 0" | bc)" -eq 1 ] && START=0

ffmpeg -y -ss "$START" -i "$INPUT" -t "$DURATION" \
  -c:v libx264 -preset fast -crf 23 "$OUTPUT"
echo "Highlight saved: $OUTPUT"
