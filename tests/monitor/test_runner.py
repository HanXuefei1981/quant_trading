"""Tests for monitor.runner module (TDD - Task 5)"""
import sys
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import monitor.runner as runner_module
from monitor.runner import CMD_MAP, TaskManager, Task


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset TaskManager singleton state before and after each test."""
    TaskManager().reset()
    yield
    TaskManager().reset()


# Helper: a _run coroutine stub that puts a done event immediately.
# These are bound as instance methods, so they receive (self, task, work_dir).
async def _fake_run_instant(self, task: Task, work_dir: Path) -> None:
    """Simulates a process that produces one output line then finishes."""
    ts = "00:00:00"
    await task.queue.put({"ts": ts, "level": "info", "msg": "hello"})
    task.exit_code = 0
    await task.queue.put({"type": "done", "exit_code": 0})
    # Clear current so is_busy becomes False
    TaskManager()._current = None


async def _fake_run_never_ends(self, task: Task, work_dir: Path) -> None:
    """Simulates a process that never finishes (stays busy)."""
    # Just wait forever — test will reset() before it returns
    await asyncio.sleep(3600)


# ---------------------------------------------------------------------------
# Test 1: CMD_MAP contains all 10 expected keys
# ---------------------------------------------------------------------------

def test_cmd_map_has_all_keys():
    expected_keys = {
        "update",
        "ingest",
        "collect",
        "fetch-fund",
        "fetch-flow",
        "phase1",
        "phase2-rolling",
        "phase2-final",
        "phase3",
        "scan",
    }
    assert set(CMD_MAP.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Test 2: TaskManager() is a singleton (same instance)
# ---------------------------------------------------------------------------

def test_task_manager_is_singleton():
    mgr1 = TaskManager()
    mgr2 = TaskManager()
    assert mgr1 is mgr2


# ---------------------------------------------------------------------------
# Test 3: start() raises ValueError for unknown command
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_raises_value_error_for_unknown_cmd(tmp_path):
    mgr = TaskManager()
    with pytest.raises(ValueError, match="Unknown command"):
        await mgr.start("nonexistent-cmd", tmp_path)


# ---------------------------------------------------------------------------
# Test 4: start() raises RuntimeError when already busy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_raises_runtime_error_when_busy(tmp_path):
    mgr = TaskManager()
    # Patch _run to never finish so mgr stays busy
    with patch.object(runner_module.TaskManager, "_run", new=_fake_run_never_ends):
        await mgr.start("update", tmp_path)
        with pytest.raises(RuntimeError, match="already running"):
            await mgr.start("ingest", tmp_path)
    # reset() is called by autouse fixture


# ---------------------------------------------------------------------------
# Test 5: start() returns Task with correct cmd and non-empty task_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_returns_task_with_correct_cmd(tmp_path):
    mgr = TaskManager()
    with patch.object(runner_module.TaskManager, "_run", new=_fake_run_instant):
        task = await mgr.start("ingest", tmp_path)
    assert task.cmd == "ingest"
    assert len(task.task_id) > 0
    assert task.task_id.isalnum() or "-" in task.task_id or len(task.task_id) >= 4


# ---------------------------------------------------------------------------
# Test 6: After start(), is_busy is True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_busy_true_after_start(tmp_path):
    mgr = TaskManager()
    with patch.object(runner_module.TaskManager, "_run", new=_fake_run_never_ends):
        await mgr.start("collect", tmp_path)
        assert mgr.is_busy is True
    # autouse fixture calls reset()


# ---------------------------------------------------------------------------
# Test 7: get_task(task_id) returns the running task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_task_returns_running_task(tmp_path):
    mgr = TaskManager()
    with patch.object(runner_module.TaskManager, "_run", new=_fake_run_never_ends):
        task = await mgr.start("phase1", tmp_path)
        retrieved = mgr.get_task(task.task_id)
        assert retrieved is task
    # autouse fixture calls reset()


# ---------------------------------------------------------------------------
# Test 8: Queue receives {"type": "done"} event when process finishes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_receives_done_event(tmp_path, monkeypatch):
    # Use real subprocess: python -c "print('hello')" — exits immediately
    monkeypatch.setitem(CMD_MAP, "scan", [sys.executable, "-c", "print('output line')"])
    mgr = TaskManager()
    task = await mgr.start("scan", tmp_path)

    events = []
    for _ in range(20):
        try:
            event = await asyncio.wait_for(task.queue.get(), timeout=5.0)
            events.append(event)
            if event.get("type") == "done":
                break
        except asyncio.TimeoutError:
            break

    done_events = [e for e in events if e.get("type") == "done"]
    assert len(done_events) == 1, f"Expected 1 done event, got events: {events}"


# ---------------------------------------------------------------------------
# Test 9: After task finishes, is_busy is False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_busy_false_after_task_finishes(tmp_path, monkeypatch):
    monkeypatch.setitem(CMD_MAP, "phase3", [sys.executable, "-c", "print('done')"])
    mgr = TaskManager()
    task = await mgr.start("phase3", tmp_path)

    # Drain queue until done event
    for _ in range(20):
        try:
            event = await asyncio.wait_for(task.queue.get(), timeout=5.0)
            if event.get("type") == "done":
                break
        except asyncio.TimeoutError:
            break

    # Give a brief moment for _current to be set to None
    await asyncio.sleep(0.05)
    assert mgr.is_busy is False
