import sys, inspect
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collectors.tdx_collector import TDXCollector


def test_tdx_collector_has_collect():
    """确认 collect() 方法存在（基础回归）。"""
    assert hasattr(TDXCollector, "collect")


def test_collect_command_does_not_call_nonexistent_fetch_all():
    """collect() 函数不应调用不存在的 TDXCollector.fetch_all()。"""
    import main as m
    source = inspect.getsource(m.collect)
    assert "fetch_all" not in source, \
        "collect() 仍在调用不存在的 .fetch_all()，请修复"
