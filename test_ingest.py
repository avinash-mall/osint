import requests
import time
with open("./sample/austin1.tif", "rb") as f:
    resp = requests.post(
        "http://localhost:8080/api/ingest/upload",
        files={"file": ("austin1.tif", f, "image/tiff")}
    )
print(resp.status_code, resp.text)
