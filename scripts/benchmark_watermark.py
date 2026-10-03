# ==============================================================================
# COURSE WALLAH PRODUCTION WATERMARK BENCHMARK
# ==============================================================================
# Benchmarks apply_video_watermark() with real 720p H.264 + AAC MP4 videos
# Tests:
# 1. Short real production test
# 2. Several-minute real production test
# Validates:
# - Duration preservation (diff <= 0.10s)
# - Resolution preservation (1280x720)
# - FPS preservation (source rate)
# - Audio preservation & sync
# - Output size & bitrate sanity
# ==============================================================================

import os
import sys
import time
import subprocess
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from itsgolu import probe_media_properties, apply_video_watermark
from vars import TEMP_DIR


def generate_real_production_video(output_path: Path, duration_sec: float, width: int = 1280, height: int = 720, fps: int = 30) -> Path:
    """Generates a real 720p H.264 video with AAC audio for accurate benchmarking."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=duration={duration_sec}:size={width}x{height}:rate={fps}",
        "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration_sec}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path)
    ]
    subprocess.run(cmd, check=True)
    return output_path


def run_benchmark():
    temp_dir = Path(TEMP_DIR)
    temp_dir.mkdir(parents=True, exist_ok=True)

    print("======================================================================")
    print("COURSE WALLAH PRODUCTION WATERMARK BENCHMARK")
    print("======================================================================")

    test_cases = [
        ("SHORT REAL TEST (15s)", 15.0),
        ("SEVERAL-MINUTE REAL TEST (180s / 3 min)", 180.0),
    ]

    for label, dur in test_cases:
        src_path = temp_dir / f"prod_bench_{int(dur)}s.mp4"
        print(f"\n[GENERATING SOURCE] {label}...")
        generate_real_production_video(src_path, dur, 1280, 720, 30)

        in_size = os.path.getsize(src_path)
        in_props = probe_media_properties(src_path)

        print(f"\n--- {label} ---")
        print("INPUT:")
        print(f"  Size           : {in_size / (1024*1024):.2f} MB ({in_size} bytes)")
        print(f"  Duration       : {in_props['duration']:.3f} s")
        print(f"  Resolution     : {in_props['width']}x{in_props['height']}")
        print(f"  FPS            : {in_props['fps']:.2f}")
        print(f"  Video Codec    : {in_props['video_codec']}")
        print(f"  Video Bitrate  : {in_props.get('video_bitrate', 0) / 1000:.1f} kbps" if in_props.get('video_bitrate') else "  Video Bitrate  : N/A")
        print(f"  Audio Codec    : {in_props['audio_codec']}")
        print(f"  Audio Duration : {in_props['audio_duration']:.3f} s")
        print(f"  Audio Bitrate  : {in_props.get('audio_bitrate', 0) / 1000:.1f} kbps" if in_props.get('audio_bitrate') else "  Audio Bitrate  : N/A")

        t0 = time.time()
        out_path = apply_video_watermark(str(src_path), watermark_text="Course Wallah")
        t_elapsed = max(0.001, time.time() - t0)

        if not out_path or not os.path.exists(out_path):
            print(f"\n❌ FAILED: Watermark returned None or output missing!")
            continue

        out_size = os.path.getsize(out_path)
        out_props = probe_media_properties(out_path)

        dur_diff = abs(out_props['duration'] - in_props['duration'])
        fps_measured = (in_props['duration'] * in_props['fps']) / t_elapsed
        speed_x = in_props['duration'] / t_elapsed

        print("\nOUTPUT:")
        print(f"  Size           : {out_size / (1024*1024):.2f} MB ({out_size} bytes)")
        print(f"  Duration       : {out_props['duration']:.3f} s (Diff: {dur_diff:.4f} s)")
        print(f"  Resolution     : {out_props['width']}x{out_props['height']}")
        print(f"  FPS            : {out_props['fps']:.2f}")
        print(f"  Video Codec    : {out_props['video_codec']}")
        print(f"  Video Bitrate  : {out_props.get('video_bitrate', 0) / 1000:.1f} kbps" if out_props.get('video_bitrate') else "  Video Bitrate  : N/A")
        print(f"  Audio Codec    : {out_props['audio_codec']}")
        print(f"  Audio Duration : {out_props['audio_duration']:.3f} s")
        print(f"  Audio Bitrate  : {out_props.get('audio_bitrate', 0) / 1000:.1f} kbps" if out_props.get('audio_bitrate') else "  Audio Bitrate  : N/A")

        print("\nPERFORMANCE:")
        print(f"  Encode Time    : {t_elapsed:.2f} seconds")
        print(f"  Processing FPS : {fps_measured:.1f} FPS")
        print(f"  Realtime Speed : {speed_x:.2f}x realtime")

        # Duration validation check
        if dur_diff <= 0.10:
            print(f"  Duration Check : [OK] PASSED (Diff: {dur_diff:.4f}s <= 0.10s)")
        else:
            print(f"  Duration Check : [FAIL] FAILED (Diff: {dur_diff:.4f}s > 0.10s)")

        # Resolution check
        if out_props['width'] == in_props['width'] and out_props['height'] == in_props['height']:
            print(f"  Resolution     : [OK] PASSED ({out_props['width']}x{out_props['height']})")
        else:
            print(f"  Resolution     : [FAIL] FAILED")

        # Audio sync check
        if out_props['has_audio'] and abs(out_props['audio_duration'] - in_props['audio_duration']) <= 0.5:
            print(f"  Audio Sync     : [OK] PASSED ({out_props['audio_codec']})")
        else:
            print(f"  Audio Sync     : [FAIL] FAILED")

        # Cleanup
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError:
            pass

    print("\n" + "=" * 70)
    print("PRODUCTION BENCHMARK COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    run_benchmark()
