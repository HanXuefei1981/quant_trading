"""Task runner — executes pipeline commands as subprocesses, streams output.

Only one task may run at a time.  Callers check `TaskManager().is_busy` and
call `TaskManager().start(cmd, work_dir)` to launch a new task.  Output is
delivered as dict events pushed onto `Task.queue`.  A final
``{"type": "done", "exit_code": N}`` event signals completion.
"""

import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

CMD_MAP: dict[str, list[str]] = {
    "update":         [sys.executable, "main.py", "update"],
    "ingest":         [sys.executable, "main.py", "ingest"],
    "collect":        [sys.executable, "main.py", "collect"],
    "fetch-fund":     [sys.executable, "main.py", "fetch-fund"],
    "fetch-flow":     [sys.executable, "main.py", "fetch-flow"],
    "phase1":         [sys.executable, "main.py", "1"],
    "phase2-rolling": [sys.executable, "main.py", "2", "--rolling"],
    "phase2-final":   [sys.executable, "main.py", "2", "--final"],
    "phase3":         [sys.executable, "main.py", "3"],
    "scan":           [sys.executable, "main.py", "scan", "--top-k", "50"],
}


@dataclass
class Task:
    task_id: str
    cmd: str
    started_at: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    exit_code: Optional[int] = None


class TaskManager:
    """Singleton task manager — only one task runs at a time."""

    _instance: Optional["TaskManager"] = None

    def __new__(cls) -> "TaskManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._current: Optional[Task] = None
        return cls._instance

    @property
    def is_busy(self) -> bool:
        return self._current is not None

    @property
    def current(self) -> Optional[Task]:
        return self._current

    async def start(self, cmd: str, work_dir: Path) -> Task:
        """Start a new task.

        Raises:
            ValueError: if *cmd* is not in CMD_MAP.
            RuntimeError: if a task is already running.
        """
        if cmd not in CMD_MAP:
            raise ValueError(f"Unknown command: {cmd}")
        if self.is_busy:
            raise RuntimeError("A task is already running")

        task = Task(
            task_id=str(uuid.uuid4())[:8],
            cmd=cmd,
            started_at=datetime.utcnow().isoformat(),
        )
        self._current = task
        asyncio.create_task(self._run(task, work_dir))
        return task

    async def _run(self, task: Task, work_dir: Path) -> None:
        """Run subprocess, stream output to queue, send done event."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *CMD_MAP[task.cmd],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(work_dir),
            )
            assert proc.stdout is not None
            async for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").rstrip()
                ts = datetime.utcnow().strftime("%H:%M:%S")
                await task.queue.put({"ts": ts, "level": "info", "msg": text})
            await proc.wait()
            task.exit_code = proc.returncode
        except Exception as exc:
            await task.queue.put(
                {
                    "ts": datetime.utcnow().strftime("%H:%M:%S"),
                    "level": "warn",
                    "msg": str(exc),
                }
            )
            task.exit_code = -1
        finally:
            await task.queue.put({"type": "done", "exit_code": task.exit_code})
            self._current = None

    def get_task(self, task_id: str) -> Optional[Task]:
        """Return the currently running task if its id matches, else None."""
        if self._current and self._current.task_id == task_id:
            return self._current
        return None

    def reset(self) -> None:
        """For testing only — reset singleton state."""
        self._current = None
