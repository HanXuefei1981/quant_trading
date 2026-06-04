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


def test_daily_registered_in_phases():
    """daily 必须在 choices 与 phases 字典中注册。"""
    import main as m
    src = inspect.getsource(m.main)
    assert '"daily"' in src or "'daily'" in src, "daily 未注册到 main()"


def test_daily_runs_steps_in_order(monkeypatch):
    """daily 顺序调用 update→fetch_fund→fetch_flow→phase1→scan，且 phase1 走增量。"""
    import main as m
    calls = []
    for name in ["update", "fetch_fund", "fetch_flow", "phase1", "scan"]:
        monkeypatch.setattr(m, name, (lambda n: (lambda args: calls.append(n)))(name))

    class A:  # 简易 args 容器
        full = True  # 故意置 True，daily 应在链中强制改为增量
    a = A()
    m.daily(a)

    assert calls == ["update", "fetch_fund", "fetch_flow", "phase1", "scan"]
    assert a.full is False, "daily 应把 args.full 置 False 让 phase1 走增量"


def test_daily_fail_fast(monkeypatch):
    """某步抛异常时，daily 应中止后续并以非零码退出 (SystemExit)。"""
    import main as m
    calls = []
    monkeypatch.setattr(m, "update", lambda args: calls.append("update"))
    def boom(args):
        calls.append("fetch_fund")
        raise RuntimeError("boom")
    monkeypatch.setattr(m, "fetch_fund", boom)
    monkeypatch.setattr(m, "fetch_flow", lambda args: calls.append("fetch_flow"))
    monkeypatch.setattr(m, "phase1", lambda args: calls.append("phase1"))
    monkeypatch.setattr(m, "scan", lambda args: calls.append("scan"))

    class A:
        full = False
    with pytest.raises(SystemExit) as exc:
        m.daily(A())
    assert exc.value.code == 1
    assert calls == ["update", "fetch_fund"]  # 在 fetch_fund 失败后中止
