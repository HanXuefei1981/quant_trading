"""Stream API router — Server-Sent Events endpoint for live task output."""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from monitor.runner import TaskManager


def create_stream_router() -> APIRouter:
    """Return an APIRouter with the /api/stream/{task_id} SSE endpoint."""
    router = APIRouter()

    @router.get("/api/stream/{task_id}")
    async def stream_task(task_id: str) -> StreamingResponse:
        manager = TaskManager()
        task = manager.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")

        async def _generate():
            while True:
                item = await task.queue.get()
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item.get("type") == "done":
                    break

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router
