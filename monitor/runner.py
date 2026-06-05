"""Task runner — executes pipeline commands as subprocesses, streams output.

Only one task may run at a time.  Callers check `TaskManager().is_busy` and
call `TaskManager().start(cmd, work_dir)` to launch a new task.  Output is
delivered as dict events pushed onto `Task.queue`.  A final
``{"type": "done", "exit_code": N}`` event signals completion.
"""

import asyncio
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Prefer the project venv so subprocesses have all dependencies installed,
# regardless of which Python was used to start the monitor server.
_PROJECT_ROOT = Path(__file__).parent.parent
_VENV_PYTHON = _PROJECT_ROOT / ".venv" / "bin" / "python3"
_PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable

# Splits on \r\n, \n, or \r — handles tqdm carriage-return progress lines.
_NEWLINE_RE = re.compile(rb'\r\n|\n|\r')

CMD_MAP: dict[str, list[str]] = {
    "update":           [_PYTHON, "main.py", "update"],
    "ingest":           [_PYTHON, "main.py", "ingest"],
    "collect":          [_PYTHON, "main.py", "collect"],
    "fetch-fund":       [_PYTHON, "main.py", "fetch-fund"],
    "fetch-flow":       [_PYTHON, "main.py", "fetch-flow"],
    "fetch-financial":  [_PYTHON, "main.py", "fetch-financial"],
    "fetch-reports":    [_PYTHON, "main.py", "fetch-reports"],
    "phase1":           [_PYTHON, "main.py", "1"],
    "phase1-full":      [_PYTHON, "main.py", "1", "--full"],
    "daily":            [_PYTHON, "main.py", "daily"],
    "phase2-rolling":   [_PYTHON, "main.py", "2", "--rolling"],
    "phase2-final":     [_PYTHON, "main.py", "2", "--final"],
    "phase3":           [_PYTHON, "main.py", "3"],
    "scan":             [_PYTHON, "main.py", "scan", "--top-k", "50"],
}


@dataclass
class Task:
    task_id: str
    cmd: str
    started_at: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    exit_code: Optional[int] = None
    proc: object = field(default=None, repr=False)  # asyncio.subprocess.Process


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
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            assert proc.stdout is not None
            task.proc = proc

            buf = b""
            while True:
                chunk = await proc.stdout.read(512)
                if not chunk:
                    # Flush any remaining partial line
                    text = buf.decode("utf-8", errors="replace").strip()
                    if text:
                        ts = datetime.utcnow().strftime("%H:%M:%S")
                        await task.queue.put({"ts": ts, "level": "info", "msg": text})
                    break
                buf += chunk
                # Split on \r\n, \n, \r — last segment may be incomplete
                parts = _NEWLINE_RE.split(buf)
                buf = parts.pop()
                for part in parts:
                    text = part.decode("utf-8", errors="replace").strip()
                    if text:
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

    async def kill(self) -> bool:
        """Terminate the running subprocess. Returns True if a signal was sent."""
        task = self._current
        if task is None or task.proc is None:
            return False
        proc = task.proc
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
        except ProcessLookupError:
            pass  # already exited
        return True

    def get_task(self, task_id: str) -> Optional[Task]:
        """Return the currently running task if its id matches, else None."""
        if self._current and self._current.task_id == task_id:
            return self._current
        return None

    def reset(self) -> None:
        """For testing only — reset singleton state."""
        self._current = None
