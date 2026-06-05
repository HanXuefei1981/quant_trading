"""Tests for monitor.readers.scan module (TDD - Task 4)"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from monitor.readers.scan import ScanData, SignalRow, get_scan


def _make_scan_csv(tmp_path: Path, date: str, rows: list[dict]) -> Path:
    """Create a fake scan CSV file under tmp_path/backtest/."""
    backtest_dir = tmp_path / "backtest"
    backtest_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    path = backtest_dir / f"scan_{date}.csv"
    df.to_csv(path, index=False)
    return path


def _make_fund_flow(tmp_path: Path, code: str, inflow_values: list[float]) -> Path:
    """Create a fake fund_flow parquet for a given stock code."""
    ff_dir = tmp_path / "fund_flow"
    ff_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range("2026-01-01", periods=len(inflow_values), freq="B")
    df = pd.DataFrame({
        "date": dates,
        "major_net_inflow": inflow_values,
        "major_net_pct": [v / 1e6 for v in inflow_values],
    })
    path = ff_dir / f"{code}.parquet"
    df.to_parquet(path, index=False)
    return path


def _sample_rows(n: int, date: str = "2026-06-04") -> list[dict]:
    """生成 n 行新格式（中文列）scan 假数据，排名递增。"""
    return [
        {
            "排名": i + 1,
            "代码": f"{300500 + i:06d}",
            "名称": f"股票{i}",
            "行业": "元器件",
            "板块": "创业板",
            "收盘价": 10.0 + i,
            "信号值": 1.5 - i * 0.01,
            "信号分位": 1.0 - i * 0.01,
            "连榜": (i % 5) + 1,
        }
        for i in range(n)
    ]


class TestScanMissingFile:
    def test_returns_empty_scan_data_when_no_scan_file_exists(self, tmp_path):
        """When no scan_*.csv file exists, should return ScanData with empty signals."""
        result = get_scan(tmp_path)
        assert isinstance(result, ScanData)
        assert result.date is None
        assert result.signals == []

    def test_preserves_top_n_and_buffer_n_when_no_file(self, tmp_path):
        """top_n and buffer_n should be returned even without a scan file."""
        result = get_scan(tmp_path, top_n=30, buffer_n=10)
        assert result.top_n == 30
        assert result.buffer_n == 10


class TestScanReadsSignals:
    def test_reads_signals_sorted_by_rank(self, tmp_path):
        """Signals should be sorted by rank ascending."""
        rows = _sample_rows(5)
        # Shuffle rows to verify sorting
        import random
        shuffled = rows.copy()
        random.shuffle(shuffled)
        _make_scan_csv(tmp_path, "2026-06-04", shuffled)

        result = get_scan(tmp_path, top_n=5, buffer_n=0)
        ranks = [s.rank for s in result.signals]
        assert ranks == sorted(ranks)

    def test_reads_correct_number_of_signals(self, tmp_path):
        """Should return top_n + buffer_n signals when enough rows exist."""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(60))
        result = get_scan(tmp_path, top_n=50, buffer_n=5)
        assert len(result.signals) == 55

    def test_returns_fewer_signals_if_not_enough_rows(self, tmp_path):
        """Should return all available rows if fewer than top_n + buffer_n."""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(10))
        result = get_scan(tmp_path, top_n=50, buffer_n=5)
        assert len(result.signals) == 10

    def test_signal_row_has_correct_fields(self, tmp_path):
        """SignalRow should have expected fields populated from CSV."""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(3))
        result = get_scan(tmp_path, top_n=3, buffer_n=0)
        row = result.signals[0]
        assert row.rank == 1
        assert row.code == "300500"
        assert row.segment == "创业板"
        assert isinstance(row.close, float)
        assert isinstance(row.signal, float)
        assert isinstance(row.signal_pct, float)
        assert row.streak == 1

    def test_code_is_zero_padded_string(self, tmp_path):
        """code must be a zero-padded 6-digit string."""
        rows = [{
            "排名": 1, "代码": "000089", "名称": "x", "行业": "银行",
            "板块": "深市主板", "收盘价": 6.76, "信号值": 1.5,
            "信号分位": 1.0, "连榜": 1,
        }]
        _make_scan_csv(tmp_path, "2026-06-04", rows)
        result = get_scan(tmp_path, top_n=1, buffer_n=0)
        assert result.signals[0].code == "000089"

    def test_code_integer_in_csv_is_zero_padded(self, tmp_path):
        """If code is stored as integer in CSV (e.g., 89), it must be zero-padded to 6 digits."""
        rows = [{
            "排名": 1, "代码": 89, "名称": "x", "行业": "银行",
            "板块": "深市主板", "收盘价": 6.76, "信号值": 1.5,
            "信号分位": 1.0, "连榜": 1,
        }]
        _make_scan_csv(tmp_path, "2026-06-04", rows)
        result = get_scan(tmp_path, top_n=1, buffer_n=0)
        assert result.signals[0].code == "000089"


class TestScanStatus:
    def test_first_top_n_rows_have_hold_status(self, tmp_path):
        """Rows with rank <= top_n should have status 'hold'."""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(10))
        result = get_scan(tmp_path, top_n=5, buffer_n=5)
        hold_rows = [s for s in result.signals if s.status == "hold"]
        assert len(hold_rows) == 5
        for s in hold_rows:
            assert s.rank <= 5

    def test_rows_after_top_n_have_buffer_status(self, tmp_path):
        """Rows with rank > top_n should have status 'buffer'."""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(10))
        result = get_scan(tmp_path, top_n=5, buffer_n=5)
        buffer_rows = [s for s in result.signals if s.status == "buffer"]
        assert len(buffer_rows) == 5
        for s in buffer_rows:
            assert s.rank > 5


class TestScanNorthBound:
    def test_north_5d_is_always_none(self, tmp_path):
        """north_5d must always be None — northbound is aggregate data."""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(5))
        result = get_scan(tmp_path, top_n=5, buffer_n=0)
        for s in result.signals:
            assert s.north_5d is None


class TestScanFundFlow:
    def test_fund_flow_none_when_parquet_missing(self, tmp_path):
        """fund_flow should be None when stock has no parquet file."""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(3))
        # No fund_flow parquet created
        result = get_scan(tmp_path, top_n=3, buffer_n=0)
        for s in result.signals:
            assert s.fund_flow is None

    def test_fund_flow_returns_latest_major_net_inflow(self, tmp_path):
        """fund_flow should return the last row's major_net_inflow from parquet."""
        code = "300500"
        rows = [{
            "排名": 1, "代码": code, "名称": "x", "行业": "元器件",
            "板块": "创业板", "收盘价": 10.0, "信号值": 1.5,
            "信号分位": 1.0, "连榜": 1,
        }]
        _make_scan_csv(tmp_path, "2026-06-04", rows)
        _make_fund_flow(tmp_path, code, [100_000.0, 200_000.0, 999_000.0])

        result = get_scan(tmp_path, top_n=1, buffer_n=0)
        assert result.signals[0].fund_flow == pytest.approx(999_000.0)

    def test_fund_flow_some_stocks_have_parquet_some_dont(self, tmp_path):
        """Mix of stocks: some with fund_flow data, some without."""
        rows = _sample_rows(3)
        _make_scan_csv(tmp_path, "2026-06-04", rows)
        # Only create parquet for code "300500"
        _make_fund_flow(tmp_path, "300500", [555_000.0])

        result = get_scan(tmp_path, top_n=3, buffer_n=0)
        assert result.signals[0].fund_flow == pytest.approx(555_000.0)
        assert result.signals[1].fund_flow is None
        assert result.signals[2].fund_flow is None


class TestScanDate:
    def test_date_extracted_from_filename(self, tmp_path):
        """ScanData.date should be the date from the latest scan filename."""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(3))
        result = get_scan(tmp_path)
        assert result.date == "2026-06-04"

    def test_picks_latest_scan_file_when_multiple_exist(self, tmp_path):
        """When multiple scan files exist, should use the one with the latest date."""
        _make_scan_csv(tmp_path, "2026-05-10", _sample_rows(3))
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(3))
        _make_scan_csv(tmp_path, "2026-04-01", _sample_rows(3))
        result = get_scan(tmp_path)
        assert result.date == "2026-06-04"


class TestScanStreakAndDegrade:
    def test_streak_populated_from_csv(self, tmp_path):
        """连榜 列应映射到 SignalRow.streak。"""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(6))
        result = get_scan(tmp_path, top_n=6, buffer_n=0)
        assert [s.streak for s in result.signals] == [1, 2, 3, 4, 5, 1]

    def test_missing_required_columns_returns_empty(self, tmp_path):
        """CSV 缺必需中文列时返回空 ScanData（不抛异常，防 /api/status 500）。"""
        bad_rows = [{"date": "2026-06-04", "code": "300500", "rank": 1, "signal": 1.5}]
        _make_scan_csv(tmp_path, "2026-06-04", bad_rows)
        result = get_scan(tmp_path)
        assert isinstance(result, ScanData)
        assert result.date == "2026-06-04"
        assert result.signals == []

    def test_segment_falls_back_when_board_column_missing(self, tmp_path):
        """板块 列缺失时 segment 回落为 '—'（必需列齐全，仅缺可选列）。"""
        rows = [{
            "排名": 1, "代码": "300500", "名称": "x", "行业": "元器件",
            "收盘价": 10.0, "信号值": 1.5, "信号分位": 1.0, "连榜": 3,
        }]  # 无 板块 列
        _make_scan_csv(tmp_path, "2026-06-04", rows)
        result = get_scan(tmp_path, top_n=1, buffer_n=0)
        assert result.signals[0].segment == "—"
        assert result.signals[0].streak == 3

    def test_streak_none_when_value_missing(self, tmp_path):
        """某行 连榜 为空(NaN) 时 streak 为 None。"""
        import numpy as np
        rows = [{
            "排名": 1, "代码": "300500", "名称": "x", "行业": "元器件",
            "板块": "创业板", "收盘价": 10.0, "信号值": 1.5,
            "信号分位": 1.0, "连榜": np.nan,
        }]
        _make_scan_csv(tmp_path, "2026-06-04", rows)
        result = get_scan(tmp_path, top_n=1, buffer_n=0)
        assert result.signals[0].streak is None
