"""
Main launcher script for VoltDrop GDSII IR-Drop Analysis & Signoff Tool.
Starts the FastAPI web server and opens the browser.
"""

import argparse
import sys
import threading
import time
import webbrowser
import uvicorn

from web.app import app, ensure_samples


def open_browser(url: str, delay: float = 1.0):
    time.sleep(delay)
    print(f"Opening browser to {url} ...")
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description="VoltDrop GDSII - IR-Drop Analysis & Margin Signoff Tool")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port number (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch web browser")
    args = parser.parse_args()

    ensure_samples()

    url = f"http://{args.host}:{args.port}"
    print("=" * 65)
    print("       [VoltDrop GDSII - IR-DROP & VALUE MARGIN TOOL]")
    print("=" * 65)
    print(f" Server running at: {url}")
    print(" Press Ctrl+C to terminate.")
    print("=" * 65)

    if not args.no_browser:
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
