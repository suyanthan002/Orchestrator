"""Convenience entry point to launch the NAO Web Dashboard."""

from web.server import run_server
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NAO Orchestrator Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Hot reload")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    args = parser.parse_args()

    run_server(host=args.host, port=args.port, reload=args.reload, open_browser=not args.no_browser)
