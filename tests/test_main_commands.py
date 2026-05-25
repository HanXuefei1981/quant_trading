import sys, inspect
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_phase1_assemble_no_use_cache():
    """phase1 不应向 assemble() 传 use_cache 参数（此参数不存在）。"""
    import main as m
    source = inspect.getsource(m.phase1)
    assert "use_cache" not in source, \
        "phase1() 仍在传递 use_cache 参数给 assemble()，请修复"


def test_ingest_registered_in_phases():
    """ingest 命令必须在 main() 的 phases 字典中注册。"""
    import main as m
    source = inspect.getsource(m.main)
    assert '"ingest"' in source or "'ingest'" in source, \
        "ingest 未在 main() 的 phases 字典中注册"


def test_ingest_function_exists():
    """ingest() 函数必须存在。"""
    import main as m
    assert hasattr(m, "ingest"), "main.py 中没有 ingest() 函数"
