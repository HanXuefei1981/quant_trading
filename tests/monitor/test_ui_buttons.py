import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HTML = (ROOT / "monitor_ui" / "index.html").read_text(encoding="utf-8")


def test_daily_button_present():
    assert "runCmd('daily')" in HTML
    assert 'id="btn-daily"' in HTML


def test_phase1_incremental_and_full_buttons_present():
    assert "runCmd('phase1')" in HTML          # 增量(日更)
    assert "runCmd('phase1-full')" in HTML      # 全量
    assert 'id="btn-phase1-full"' in HTML


def test_section_labels_present():
    assert "日常更新" in HTML
    assert "全量重建与训练" in HTML


def test_no_orphan_old_phase1_label():
    # 旧的单一"Phase 1 特征计算"按钮文案应已被拆分替换
    assert "Phase 1 特征计算" not in HTML


def test_existing_collection_buttons_preserved():
    # 重排不得丢失既有按钮
    for cmd in ["update", "fetch-fund", "fetch-flow", "collect",
                "fetch-financial", "fetch-reports", "scan",
                "phase2-rolling", "phase2-final", "phase3"]:
        assert f"runCmd('{cmd}')" in HTML, f"按钮 {cmd} 丢失"
        assert f'id="btn-{cmd}"' in HTML, f"按钮 id btn-{cmd} 丢失"
