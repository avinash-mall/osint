import requests
import time
import numpy as np
from PIL import Image
import io
import sys

# Create a random dummy image
img_array = np.random.randint(0, 255, (1024, 1024, 3), dtype=np.uint8)
img = Image.fromarray(img_array)
buf = io.BytesIO()
img.save(buf, format="PNG")
img_bytes = buf.getvalue()

URL = "http://localhost:8007/detect"

print("Sending first request (initialization overhead expected)...")
start = time.time()
try:
    resp = requests.post(URL, files={"image": ("test.png", img_bytes, "image/png")})
    resp.raise_for_status()
    print(f"First request: {time.time() - start:.2f}s")
    print("Detections count:", len(resp.json().get("detections", [])))
except Exception as e:
    print("Error:", e)
    sys.exit(1)

print("Running 5 performance requests...")
times = []
for i in range(5):
    start = time.time()
    try:
        resp = requests.post(URL, files={"image": ("test.png", img_bytes, "image/png")})
        resp.raise_for_status()
        t = time.time() - start
        times.append(t)
        print(f"Request {i+1}: {t:.2f}s")
    except Exception as e:
        print("Error:", e)

if times:
    print(f"Average time per request (after warmup): {sum(times)/len(times):.2f}s")
    print(f"Max FPS approx: {1.0/(sum(times)/len(times)):.2f}")
