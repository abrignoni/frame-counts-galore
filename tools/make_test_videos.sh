#!/bin/sh
# Regenerates test_videos/synth and test_videos/corrupt with ffmpeg.
# The committed files are the reference set; re-running this produces files with the
# same frame counts but not necessarily the same bytes, because encoder output depends
# on the ffmpeg build. Run from the repository root.
set -e
OUT=test_videos
mkdir -p "$OUT/synth" "$OUT/corrupt"
cd "$OUT/synth"

gen() { name=$1; n=$2; shift 2
  ffmpeg -v error -y -f lavfi -i "testsrc2=size=320x240:rate=${RATE:-30}" -frames:v "$n" "$@" "$name"
  echo "made $name ($n frames)"; }

RATE=30        gen h264_bframes_137.mp4    137 -c:v libx264 -bf 3 -g 30 -pix_fmt yuv420p
RATE=30        gen h264_nobframes_137.mp4  137 -c:v libx264 -bf 0 -g 30 -pix_fmt yuv420p
RATE=30000/1001 gen h264_ntsc_301.mp4      301 -c:v libx264 -bf 2 -pix_fmt yuv420p
RATE=24000/1001 gen h264_23976_240.mov     240 -c:v libx264 -bf 2 -pix_fmt yuv420p
RATE=30        gen hevc_bframes_250.mp4    250 -c:v libx265 -x265-params log-level=none -bf 4 -pix_fmt yuv420p
RATE=30        gen vp9_200.webm            200 -c:v libvpx-vp9 -deadline realtime -cpu-used 8 -pix_fmt yuv420p
RATE=25        gen mpeg2_180.ts            180 -c:v mpeg2video -bf 2 -pix_fmt yuv420p
RATE=25        gen mjpeg_90.avi             90 -c:v mjpeg -q:v 5 -pix_fmt yuvj420p
RATE=30        gen prores_60.mov            60 -c:v prores_ks -profile:v 3 -pix_fmt yuv422p10le
RATE=30        gen h264_1frame.mp4           1 -c:v libx264 -pix_fmt yuv420p
RATE=60        gen h264_60fps_600.mp4      600 -c:v libx264 -bf 3 -pix_fmt yuv420p
RATE=30        gen mkv_h264_bframes_333.mkv 333 -c:v libx264 -bf 3 -pix_fmt yuv420p
RATE=30        gen h264_odd_321x241_50.mp4  50 -c:v libx264 -pix_fmt yuv420p -vf "scale=322:242,crop=321:241"
ffmpeg -v error -y -f lavfi -i "testsrc2=size=1920x1080:rate=30" -frames:v 300 -c:v libx264 -bf 3 -pix_fmt yuv420p h264_1080p_300.mp4

# Variable frame rate: 90 frames at 30 fps followed by 45 frames at 15 fps, stream-copied together.
ffmpeg -v error -y -f lavfi -i "testsrc2=size=320x240:rate=30" -frames:v 90 -c:v libx264 -bf 2 -pix_fmt yuv420p seg30.mp4
ffmpeg -v error -y -f lavfi -i "testsrc2=size=320x240:rate=15" -frames:v 45 -c:v libx264 -bf 2 -pix_fmt yuv420p seg15.mp4
printf "file 'seg30.mp4'\nfile 'seg15.mp4'\n" > concat.txt
ffmpeg -v error -y -f concat -safe 0 -i concat.txt -c copy vfr_concat_135.mp4 && rm concat.txt

# Damaged copies.
cd ../corrupt
ffmpeg -v error -y -i ../synth/h264_bframes_137.mp4 -c copy -movflags +faststart h264_bframes_137_faststart.mp4
python3 - <<'PY'
def damage(src, dst, fill, marker=b"mdat", span=2048):
    d = open(src, "rb").read(); b = bytearray(d)
    i = d.find(marker) if marker else 0
    m = i + (len(d) - i) // 2
    for k in range(m, m + span): b[k] = fill
    open(dst, "wb").write(b)
def truncate(src, dst, keep):
    d = open(src, "rb").read(); open(dst, "wb").write(d[: int(len(d) * keep)])
damage("../synth/h264_nobframes_137.mp4", "h264_nobframes_137_midcorrupt.mp4", 0xA5)
truncate("../synth/h264_nobframes_137.mp4", "h264_nobframes_137_truncated.mp4", 0.7)
damage("h264_bframes_137_faststart.mp4", "h264_bframes_137_faststart_midcorrupt.mp4", 0x33)
truncate("h264_bframes_137_faststart.mp4", "h264_bframes_137_faststart_truncated.mp4", 0.7)
damage("../synth/mpeg2_180.ts", "mpeg2_180_midcorrupt.ts", 0x5A, marker=None, span=4096)
truncate("../synth/mpeg2_180.ts", "mpeg2_180_truncated.ts", 0.6)
PY
echo "done"
