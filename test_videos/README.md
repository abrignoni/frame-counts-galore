# Test videos

Small videos with known frame counts for checking the tool. All were generated with ffmpeg's `testsrc2` source by `tools/make_test_videos.sh`, and every count below was confirmed against `ffprobe -count_frames`.

## synth/

The number in each filename is the exact frame count.

| File | Codec / container | Frames | Notes |
|---|---|---:|---|
| h264_bframes_137.mp4 | H.264, MP4 | 137 | B-frames, keyframe every 30 |
| h264_nobframes_137.mp4 | H.264, MP4 | 137 | no B-frames |
| h264_ntsc_301.mp4 | H.264, MP4 | 301 | 29.97 fps |
| h264_23976_240.mov | H.264, MOV | 240 | 23.976 fps |
| h264_60fps_600.mp4 | H.264, MP4 | 600 | 60 fps |
| h264_1frame.mp4 | H.264, MP4 | 1 | single frame |
| h264_odd_321x241_50.mp4 | H.264, MP4 | 50 | odd width and height |
| h264_1080p_300.mp4 | H.264, MP4 | 300 | 1920x1080, the slow one in full forensic mode |
| hevc_bframes_250.mp4 | HEVC, MP4 | 250 | B-frames |
| vp9_200.webm | VP9, WebM | 200 | container carries no frame count |
| mpeg2_180.ts | MPEG-2, MPEG-TS | 180 | container carries no frame count |
| mjpeg_90.avi | MJPEG, AVI | 90 | yuvj420p |
| prores_60.mov | ProRes 422 HQ, MOV | 60 | 10-bit, yuv422p10le |
| mkv_h264_bframes_333.mkv | H.264, MKV | 333 | container carries no frame count |
| vfr_concat_135.mp4 | H.264, MP4 | 135 | variable frame rate: 90 frames at 30 fps then 45 at 15 fps |
| seg30.mp4, seg15.mp4 | H.264, MP4 | 90, 45 | the two pieces vfr_concat_135.mp4 was made from |

## corrupt/

Damaged copies of the files above. Expected results:

| File | Frames | Rejected packets | Notes |
|---|---:|---:|---|
| h264_bframes_137_faststart.mp4 | 137 | 0 | intact control, index moved to the front of the file |
| h264_bframes_137_faststart_midcorrupt.mp4 | 135 | 2 | 2 KB overwritten in the middle of the media data |
| h264_nobframes_137_midcorrupt.mp4 | 135 | 2 | same damage, no B-frames |
| h264_bframes_137_faststart_truncated.mp4 | 96 | 1 | last 30% of the file cut off |
| h264_nobframes_137_truncated.mp4 | not opened | | last 30% cut off and the index was at the end, so FFmpeg cannot open it; listed under `files_skipped` in the manifest |
| mpeg2_180_midcorrupt.ts | 178 | 0 | 4 KB overwritten; the MPEG-2 decoder conceals without rejecting a packet |
| mpeg2_180_truncated.ts | 71 | 0 | last 40% cut off |

Frames after a rejected packet and before the next keyframe are error-concealed by the decoder. Their `native_sha256` values are reproducible with this tool's fixed single-thread decoder configuration but differ from ffmpeg's default threaded decoding; frame counts do not.
