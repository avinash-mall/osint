import time
import requests
import numpy as np
from PIL import Image
import io
import sys

def create_dummy_image(size=(1024, 1024)):
    img_array = np.random.randint(0, 255, (size[0], size[1], 3), dtype=np.uint8)
    img = Image.fromarray(img_array)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def test_endpoint(name, url):
    print(f"Testing {name} at {url}...")
    try:
        # Create a new dummy image for each request to avoid caching effects, though unlikely
        img_bytes = create_dummy_image()
        
        # Warmup
        print("  Warmup run...")
        requests.post(url, files={"image": ("test.png", img_bytes, "image/png")}, data={"metadata": "{}"}, timeout=300)
        
        # Benchmark
        runs = 5
        total_time = 0
        for i in range(runs):
            img_bytes = create_dummy_image()
            t0 = time.time()
            resp = requests.post(url, files={"image": ("test.png", img_bytes, "image/png")}, data={"metadata": "{}"}, timeout=300)
            elapsed = time.time() - t0
            
            if resp.status_code != 200:
                print(f"  Run {i+1}: Failed with status {resp.status_code} - {resp.text}")
            else:
                print(f"  Run {i+1}: {elapsed:.3f}s")
                total_time += elapsed
                
        print(f"  Average time: {total_time/runs:.3f}s\n")
    except Exception as e:
        print(f"  Failed to test {name}: {e}\n")

if __name__ == "__main__":
    providers = {
        "YOLO": "http://localhost:8002/detect",
        "LAE-DINO": "http://localhost:8004/detect",
        "MMRotate": "http://localhost:8005/detect",
        "LSKNet": "http://localhost:8006/detect"
    }
    
    for name, url in providers.items():
        test_endpoint(name, url)
