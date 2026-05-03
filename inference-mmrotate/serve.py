import argparse
import os
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MMRotate inference API.")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8001")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("WEB_CONCURRENCY", "1")))
    args = parser.parse_args()

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
