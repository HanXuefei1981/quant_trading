"""Status API router — aggregates all reader data into a single JSON response."""
import dataclasses
from pathlib import Path

from fastapi import APIRouter


def create_status_router(data_dir: Path) -> APIRouter:
    """Return an APIRouter with the /api/status endpoint bound to *data_dir*."""
    router = APIRouter()

    @router.get("/api/status")
    async def get_status() -> dict:
        from monitor.readers.watermark import get_watermarks
        from monitor.readers.metrics import get_metrics
        from monitor.readers.backtest import get_backtest
        from monitor.readers.scan import get_scan

        wm = get_watermarks(data_dir)
        mt = get_metrics(data_dir)
        bt = get_backtest(data_dir)
        sc = get_scan(data_dir)

        return {
            "watermarks": dataclasses.asdict(wm),
            "metrics": dataclasses.asdict(mt),
            "backtest": dataclasses.asdict(bt),
            "scan": dataclasses.asdict(sc),
        }

    return router
