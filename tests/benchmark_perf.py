import sys
import os
import time
import shutil
import uuid
import json
import subprocess
from pathlib import Path

# Add parent directory to path so we can import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app

def generate_test_video(filename="test_video.mp4"):
    if os.path.exists(filename):
        return
    print(f"Generating {filename}...")
    subprocess.run([
        "ffmpeg", "-f", "lavfi", "-i", "testsrc=duration=10:size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=10",
        "-c:v", "libx264", "-c:a", "aac", "-strict", "-2", "-y", filename
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def benchmark():
    print("Starting benchmark...")

    # Ensure test video exists
    generate_test_video()

    # Setup
    job_id = "bench_test"
    job_dir = app.DOWNLOADS_DIR / job_id
    # Clean up previous run
    if job_dir.exists():
        shutil.rmtree(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    proc_dir = app.PROCESSING_DIR / job_id
    if proc_dir.exists():
        shutil.rmtree(proc_dir)

    output_file = app.OUTPUT_DIR / f"alert_{job_id}.mp4"
    if output_file.exists():
        output_file.unlink()

    # Copy test video to job dir
    shutil.copy("test_video.mp4", job_dir / "clip.mp4")

    # Define parameters
    # Test video is 1280x720. Let's crop center.
    crop_x = 280
    crop_y = 0
    crop_width = 720
    crop_height = 720
    trim_start = 1.0
    trim_end = 9.0 # 8 seconds duration
    use_separate_audio = False
    use_static_image = False

    # Settings
    resolution = 720
    buffer_duration = 0 # Disable buffer to focus on the optimized part

    # Measure time
    start_time = time.time()

    app.run_processing_pipeline(
        job_id, crop_x, crop_y, crop_width, crop_height,
        trim_start, trim_end, use_separate_audio, use_static_image,
        resolution=resolution, buffer_duration=buffer_duration, normalize_audio=True
    )

    end_time = time.time()
    duration = end_time - start_time

    print(f"Benchmark finished in {duration:.4f} seconds")

    # Verify output
    if output_file.exists():
        print(f"Output file created: {output_file} ({output_file.stat().st_size} bytes)")
    else:
        print("Output file NOT created!")

    # Check intermediate files
    if proc_dir.exists():
        intermediate_files = list(proc_dir.glob("*"))
        print("Intermediate files:", sorted([f.name for f in intermediate_files]))
    else:
        print("Processing directory does not exist.")

if __name__ == "__main__":
    benchmark()
