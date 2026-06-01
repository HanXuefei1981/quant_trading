"""Run API router — launches pipeline commands as background subprocesses."""
from pathlib import Path

from fastapi import APIRouter, HTTPException

from monitor.runner import TaskManager


def create_run_router(work_dir: Path) -> APIRouter:
    """Return an APIRouter with the /api/run/{cmd} endpoint bound to *work_dir*."""
    router = APIRouter()

    @router.post("/api/run/{cmd}")
    async def run_cmd(cmd: str) -> dict:
        manager = TaskManager()
        if manager.is_busy:
            raise HTTPException(status_code=409, detail="A task is already running")
        try:
            task = await manager.start(cmd, work_dir)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "task_id": task.task_id,
            "cmd": task.cmd,
            "started_at": task.started_at,
        }

    @router.post("/api/kill")
    async def kill_task() -> dict:
        manager = TaskManager()
        if not manager.is_busy:
            raise HTTPException(status_code=404, detail="No task is running")
        killed = await manager.kill()
        return {"killed": killed}

    return router
