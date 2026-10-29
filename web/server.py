"""NAO Web Server application."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from web.api import api_router

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(
        title="NAO — Nexora Agent Orchestrator",
        description="Multi-agent orchestration control plane and workflow dashboard",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Enable CORS for local and development environments
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routes
    app.include_router(api_router)

    # Mount static assets (CSS, JS, images, icons)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return {
            "name": "NAO Orchestrator API",
            "version": "1.0.0",
            "docs": "/docs",
            "status": "ready",
        }

    return app


app = create_app()


def run_server(host: str = "127.0.0.1", port: int = 8000, reload: bool = False, open_browser: bool = True) -> None:
    """Run the web server with uvicorn and optionally open the browser."""
    import uvicorn
    import webbrowser
    import threading
    import time

    url = f"http://{host}:{port}"
    print("\n" + "=" * 62)
    print(f" [*] NAO Web Dashboard is starting!")
    print(f" [*] Open this link in your browser:")
    print(f"     --> {url}")
    print(f" [*] Interactive API Docs: {url}/docs")
    print("=" * 62 + "\n")

    if open_browser:
        def _open():
            time.sleep(1.0)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run("web.server:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NAO Web Dashboard Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable hot reload")
    parser.add_argument("--no-browser", action="store_true", help="Don't automatically open the browser")
    args = parser.parse_args()

    run_server(host=args.host, port=args.port, reload=args.reload, open_browser=not args.no_browser)
