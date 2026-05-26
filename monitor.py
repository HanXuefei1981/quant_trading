#!/usr/bin/env python3
"""Web monitoring dashboard for quant_trading.

Usage:
    python monitor.py [--host HOST] [--port PORT]
"""
import argparse
from pathlib import Path

ROOT_DIR = Path(__file__).parent
DATA_DIR = ROOT_DIR / "data"
UI_DIR = ROOT_DIR / "monitor_ui"


def create_app(data_dir: Path = DATA_DIR, root_dir: Path = ROOT_DIR):
    """Create and configure the FastAPI application.

    Args:
        data_dir: Path to the data directory (readers use this).
        root_dir: Project root used as the working directory for task execution.

    Returns:
        Configured FastAPI instance with all routes and static file mount.
    """
    import uvicorn  # noqa: F401 — ensure uvicorn is importable at startup
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    from monitor.api.status import create_status_router
    from monitor.api.run import create_run_router
    from monitor.api.stream import create_stream_router

    app = FastAPI(title="Quant Monitor")

    app.include_router(create_status_router(data_dir))
    app.include_router(create_run_router(root_dir))
    app.include_router(create_stream_router())

    # Serve the frontend — html=True makes / serve index.html
    ui_path = str(UI_DIR)
    app.mount("/", StaticFiles(directory=ui_path, html=True), name="ui")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Quant Trading Monitor Dashboard")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8765, help="Bind port")
    args = parser.parse_args()

    import uvicorn

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
