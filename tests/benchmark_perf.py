import sys
import os
import time
import shutil
import subprocess

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

    client = app.app.test_client()

    with open("test_video.mp4", "rb") as video_file:
        upload_response = client.post(
            "/api/upload-video",
            data={"video": (video_file, "test_video.mp4")},
            content_type="multipart/form-data",
        )

    upload_data = upload_response.get_json()
    if upload_response.status_code != 200 or not upload_data.get("job_id"):
        raise RuntimeError(f"Upload failed: {upload_data}")

    job_id = upload_data["job_id"]
    job_dir = app.DOWNLOADS_DIR / job_id
    proc_dir = app.PROCESSING_DIR / job_id
    output_file = app.OUTPUT_DIR / f"alert_{job_id}.mp4"

    if proc_dir.exists():
        shutil.rmtree(proc_dir)
    if output_file.exists():
        output_file.unlink()

    payload = {
        "job_id": job_id,
        "crop": {
            "x": 280,
            "y": 0,
            "width": 720,
            "height": 720,
            "ratio": "1:1",
        },
        "trim_start": 1.0,
        "trim_end": 9.0,
        "use_separate_audio": False,
        "use_static_image": False,
        "settings": {
            "resolution": 720,
            "bufferDuration": 0,
            "normalizeAudio": True,
        },
    }

    start_time = time.time()
    process_response = client.post("/api/process", json=payload)
    process_data = process_response.get_json()
    if process_response.status_code != 200 or process_data.get("status") != "processing":
        raise RuntimeError(f"Process start failed: {process_data}")

    deadline = time.time() + 180
    final_status = None
    while time.time() < deadline:
        status_response = client.get(f"/api/status/{job_id}")
        final_status = status_response.get_json()
        if final_status.get("status") in {"complete", "error"}:
            break
        time.sleep(0.5)

    duration = time.time() - start_time
    print(f"Benchmark finished in {duration:.4f} seconds")
    print(f"Final job status: {final_status}")

    if output_file.exists():
        print(f"Output file created: {output_file} ({output_file.stat().st_size} bytes)")
    else:
        print("Output file NOT created!")

    if proc_dir.exists():
        intermediate_files = list(proc_dir.glob("*"))
        print("Intermediate files:", sorted(f.name for f in intermediate_files))
    else:
        print("Processing directory does not exist.")

    if final_status is None or final_status.get("status") != "complete":
        raise RuntimeError(f"Benchmark did not complete successfully: {final_status}")

if __name__ == "__main__":
    benchmark()
