import sys, inspect
import pytest
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


def test_full_flag_registered():
    """argparse 必须注册 --full 标志（dest=full）。"""
    import main as m
    src = inspect.getsource(m.main)
    assert '"--full"' in src or "'--full'" in src, "main() 未注册 --full"


def test_phase1_branches_on_full():
    """phase1 必须依据 args.full 在 assemble_incremental 与 assemble 间分支。"""
    import main as m
    src = inspect.getsource(m.phase1)
    assert "assemble_incremental" in src, "phase1 未走增量 assemble_incremental"
    assert "assemble(" in src, "phase1 未保留全量 assemble()"
    assert "full" in src, "phase1 未根据 args.full 分支"


def test_phase1_full_writes_watermark():
    """phase1 全量分支必须回写水位（调用 write_features_watermark）。"""
    import main as m
    src = inspect.getsource(m.phase1)
    assert "write_features_watermark" in src, "phase1 全量分支未回写 features 水位"
