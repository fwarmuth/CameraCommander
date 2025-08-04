#!/usr/bin/env bash
# gifify-small.sh — shrink GIFs or convert video→small GIF via ffmpeg
# Defaults: 480px wide, 12 fps, 256 colors, quality dithering.
set -euo pipefail

command -v ffmpeg >/dev/null || { echo "ffmpeg not found" >&2; exit 1; }

WIDTH=480
FPS=12
MAXC=256
DITHER="sierra2_4a"   # try: bayer (with -b), or none
BAYER_SCALE=2         # only used for DITHER=bayer (0..5)
STATS="diff"          # palettegen: diff|full|single
CROP=""
START=""
DUR=""
LOOPS=0               # GIF loops: -1 play once, 0 infinite (default per muxer)
OUT=""

usage() {
  cat <<EOF
Usage: $0 [-w WIDTH] [-f FPS] [-n MAX_COLORS] [-d DITHER] [-b BAYER_SCALE]
          [-c CROP] [-s START] [-t DURATION] [-L LOOPS] [-o OUT.gif] INPUT.(gif|mp4|mov|mkv|...)
Examples:
  $0 input.mp4                   # quick convert → small GIF
  $0 -w 360 -f 10 -n 128 clip.mp4
  $0 -c 300:200:50:50 input.gif  # crop then shrink
  $0 -s 2 -t 3.5 video.mp4       # trim 3.5s from 2s
EOF
}

while getopts ":w:f:n:d:b:c:s:t:L:o:h" opt; do
  case "$opt" in
    w) WIDTH="$OPTARG" ;;
    f) FPS="$OPTARG" ;;
    n) MAXC="$OPTARG" ;;
    d) DITHER="$OPTARG" ;;
    b) BAYER_SCALE="$OPTARG" ;;
    c) CROP="$OPTARG" ;;
    s) START="$OPTARG" ;;
    t) DUR="$OPTARG" ;;
    L) LOOPS="$OPTARG" ;;
    o) OUT="$OPTARG" ;;
    h) usage; exit 0 ;;
    \?) echo "Unknown option: -$OPTARG" >&2; usage; exit 1 ;;
    :)  echo "Option -$OPTARG requires an argument." >&2; usage; exit 1 ;;
  esac
done
shift $((OPTIND-1))

[ $# -ge 1 ] || { usage; exit 1; }
IN="$1"
[ -f "$IN" ] || { echo "Input not found: $IN" >&2; exit 1; }
[ -n "$OUT" ] || OUT="$(basename "${IN%.*}")_small.gif"

SS=(); [ -n "$START" ] && SS=(-ss "$START")
TT=(); [ -n "$DUR"   ] && TT=(-t "$DUR")

VF="fps=${FPS}"
[ -n "$CROP" ] && VF="${VF},crop=${CROP}"
VF="${VF},scale=${WIDTH}:-1:flags=lanczos"

PU="paletteuse"
if [[ "$DITHER" == "bayer" ]]; then
  PU+="=dither=bayer:bayer_scale=${BAYER_SCALE}:diff_mode=rectangle"
elif [[ "$DITHER" != "default" ]]; then
  PU+="=dither=${DITHER}"
fi

ffmpeg -hide_banner -loglevel error \
  "${SS[@]}" -i "$IN" "${TT[@]}" -an \
  -filter_complex "[0:v]${VF},split[s0][s1];[s0]palettegen=max_colors=${MAXC}:stats_mode=${STATS}[p];[s1][p]${PU}" \
  -loop "${LOOPS}" -map_metadata -1 -y "$OUT"

echo "Wrote: $OUT"
