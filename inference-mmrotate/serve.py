import argparse
import os
import subprocess
import sys


def warmup() -> None:
    try:
        import main
        import numpy as np
        if main.DEVICE.startswith("cuda"):
            print("[WARMUP] Starting sequential PTX JIT compilation warmup...", flush=True)
            main.load_model()
            dummy_image = np.zeros((1024, 1024, 3), dtype=np.uint8)
            main.run_inference(dummy_image, (1024, 1024), {})
            print("[WARMUP] PTX JIT compilation successful. Kernels cached.", flush=True)
    except Exception as e:
        print(f"[WARMUP] Warmup failed: {e}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MMRotate inference API.")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8001")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("WEB_CONCURRENCY", "1")))
    args = parser.parse_args()

    warmup()

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--workers",
        str(max(1, args.workers)),
    ]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
