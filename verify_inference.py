import time
import requests
import numpy as np
from PIL import Image
import io
import sys

def create_dummy_image(size=(1024, 1024)):
    """Creates a random dummy image to simulate an input chip."""
    img_array = np.random.randint(0, 255, (size[0], size[1], 3), dtype=np.uint8)
    img = Image.fromarray(img_array)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def test_endpoint(name, url):
    print(f"--- Testing {name} at {url} ---")
    try:
        # Create a dummy image for the request
        img_bytes = create_dummy_image()
        
        # 1. Functional Validation & Warmup
        print("  Warmup run (validating connectivity and model load)...", end=" ", flush=True)
        start_warmup = time.time()
        resp = requests.post(url, files={"image": ("test.png", img_bytes, "image/png")}, data={"metadata": "{}"}, timeout=300)
        warmup_time = time.time() - start_warmup
        
        if resp.status_code != 200:
            print(f"FAILED (Status: {resp.status_code})")
            print(f"  Response: {resp.text}")
            return None
        
        data = resp.json()
        if "detections" not in data:
            print("FAILED (Response missing 'detections' key)")
            return None
        
        print(f"OK ({warmup_time:.2f}s)")
        
        # 2. Performance Benchmarking
        runs = 5
        latencies = []
        print(f"  Running {runs} performance tests...", end=" ", flush=True)
        
        for i in range(runs):
            # New image per run to avoid any potential server-side caching
            current_img = create_dummy_image()
            t0 = time.time()
            resp = requests.post(url, files={"image": ("test.png", current_img, "image/png")}, data={"metadata": "{}"}, timeout=300)
            elapsed = time.time() - t0
            
            if resp.status_code == 200:
                latencies.append(elapsed)
            else:
                print(f"\n  Run {i+1} failed with status {resp.status_code}")
        
        if not latencies:
            print("FAILED (No successful runs)")
            return None
            
        avg_latency = sum(latencies) / len(latencies)
        fps = 1.0 / avg_latency
        print("DONE")
        print(f"  Average Latency: {avg_latency:.3f}s")
        print(f"  Estimated Throughput: {fps:.2f} FPS")
        
        return {
            "name": name,
            "avg_latency": avg_latency,
            "fps": fps,
            "status": "Healthy"
        }
        
    except Exception as e:
        print(f"  ERROR: {e}")
        return None

if __name__ == "__main__":
    providers = {
        "YOLO": "http://localhost:8002/detect",
        "LAE-DINO": "http://localhost:8004/detect",
        "MMRotate": "http://localhost:8005/detect",
        "LSKNet": "http://localhost:8006/detect",
        "SAM2": "http://localhost:8007/detect"
    }
    
    results = []
    print("Starting Inference Endpoint Validation and Benchmarking...\n")
    
    for name, url in providers.items():
        res = test_endpoint(name, url)
        if res:
            results.append(res)
        print()

    print("="*50)
    print(f"{'Provider':<15} | {'Avg Latency':<15} | {'FPS':<10} | {'Status':<10}")
    print("-" * 50)
    for r in results:
        print(f"{r['name']:<15} | {r['avg_latency']:<15.3f} | {r['fps']:<10.2f} | {r['status']:<10}")
    print("="*50)
