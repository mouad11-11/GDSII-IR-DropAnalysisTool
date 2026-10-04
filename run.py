#!/usr/bin/env python3
import argparse
import threading
import time
import webbrowser
import uvicorn

from web.app import app, ensure_samples


def open_browser(url: str, delay: float = 0.8):
    time.sleep(delay)
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description="VoltDrop GDSII Web Server")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not launch browser")
    args = parser.parse_args()

    ensure_samples()

    url = f"http://{args.host}:{args.port}"
    print(f"VoltDrop server listening on {url}")

    if not args.no_browser:
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
