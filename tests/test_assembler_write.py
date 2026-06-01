"""Tests for assembler.py write-path DAL migration."""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch
import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.feature_repo import FeatureRepo


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    migrate(c)
    yield c
    c.close()


def _seed_kline(conn, code: str = "000001", n: int = 300) -> None:
    from datetime import timedelta
    base = date(2023, 1, 1)
    for i in range(n):
        d = base + timedelta(days=i)
        conn.execute(
            "INSERT OR IGNORE INTO kline VALUES (?, ?, 10.0, 11.0, 9.5, 10.5, 1e8, 1e6)",
            [d.isoformat(), code],
        )


def _make_combined(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "code": code,
        "close": [10.5, 11.0],
        "future_ret": [0.01, 0.02],
        "ret1": [0.005, 0.01],
        "label": [1, 2],
    })


# ── assemble() writes to FeatureRepo ─────────────────────────────────────────

def test_assemble_writes_to_feature_repo(conn):
    from src.features.assembler import assemble

    _seed_kline(conn, "000001", n=300)
    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    combined = _make_combined()

    with patch("src.features.assembler.add_all_features", return_value=combined), \
         patch("src.features.assembler.add_report_features", side_effect=lambda df, c, **kw: df), \
         patch("src.features.assembler.add_signal_features", side_effect=lambda df, c, **kw: df), \
         patch("src.features.assembler.add_cross_sectional_label", side_effect=lambda df: df.assign(label=1)), \
         patch("src.features.assembler.preprocess_features", side_effect=lambda df, cols: df), \
         patch("src.features.assembler._load_northbound", return_value=None), \
         patch("src.features.assembler._load_lhb_all", return_value=None):
        assemble(raw_repo=raw_repo, feature_repo=feature_repo)

    date_range = feature_repo.get_feature_date_range()
    assert date_range is not None, "features table should have data after assemble()"


# ── 逐日流式预处理写表：内存优化，结果须与整表预处理逐字节一致 ──────────────────

def test_preprocess_and_write_streams_per_date_equivalent(conn):
    """流式逐日预处理写表 == 整表预处理后写表（所有算子均为按日截面，数学等价）。

    这是 Phase 1 内存优化的正确性保证：把 ~3.3GB 整表特征矩阵拆成逐日 ~MB 切片，
    峰值内存从 13.7GB 降到 combined 本身，但写入表的数值必须与旧路径完全一致。
    """
    from src.features.assembler import _preprocess_and_write
    from src.features.preprocessing import preprocess_features

    # 两个交易日、每日两只不同板块股票（沪市主板 600000 / 深市主板 000001）
    df = pd.DataFrame({
        "date": pd.to_datetime(
            ["2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03"]
        ),
        "code": ["000001", "600000", "000001", "600000"],
        "f1": [1.0, 2.0, 3.0, 4.0],
        "f2": [10.0, 25.0, 30.0, 40.0],
        "label": [1, 0, 2, 1],
    })
    feature_cols = ["f1", "f2"]

    # 期望：整表一次性预处理（旧路径）
    expected = (
        preprocess_features(df, feature_cols)
        .sort_values(["date", "code"])
        .reset_index(drop=True)
    )

    # 实际：逐日流式预处理并写表（新路径）
    feature_repo = FeatureRepo(conn)
    written = _preprocess_and_write(df, feature_cols, feature_repo)
    assert written == len(df), "写入行数须等于输入行数"

    got = conn.execute(
        "SELECT date, code, f1, f2, segment FROM features ORDER BY date, code"
    ).df()

    for col in ("f1", "f2"):
        pd.testing.assert_series_equal(
            got[col].reset_index(drop=True),
            expected[col].reset_index(drop=True),
            check_names=False,
            rtol=1e-12,
            atol=1e-12,
        )
    assert list(got["segment"]) == list(expected["segment"])


# ── assemble() is idempotent ──────────────────────────────────────────────────

def test_assemble_is_idempotent(conn):
    from src.features.assembler import assemble

    _seed_kline(conn, "000001", n=300)
    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    combined = _make_combined()

    import contextlib
    patches = [
        patch("src.features.assembler.add_all_features", return_value=combined),
        patch("src.features.assembler.add_report_features", side_effect=lambda df, c, **kw: df),
        patch("src.features.assembler.add_signal_features", side_effect=lambda df, c, **kw: df),
        patch("src.features.assembler.add_cross_sectional_label", side_effect=lambda df: df.assign(label=1)),
        patch("src.features.assembler.preprocess_features", side_effect=lambda df, cols: df),
        patch("src.features.assembler._load_northbound", return_value=None),
        patch("src.features.assembler._load_lhb_all", return_value=None),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        assemble(raw_repo=raw_repo, feature_repo=feature_repo)
        assemble(raw_repo=raw_repo, feature_repo=feature_repo)  # 第二次：幂等

    result = conn.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    assert result == len(combined), "idempotent upsert must not duplicate rows"


# ── assemble_inference() 读 features 表最新截面（含无标签最近日，不二次预处理）──────

def test_assemble_inference_returns_latest_date(conn):
    """推断截面 = features 表最新日；表中已预处理，直接返回不再二次预处理。"""
    from src.features.assembler import assemble_inference

    feature_repo = FeatureRepo(conn)
    df1 = _make_combined("000001")   # 2024-01-02, 2024-01-03
    # 最新日 2024-01-05 为无标签行（label=NaN，模拟 Phase 1 写入的最近交易日）
    df2 = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-05"]),
        "code": "000002",
        "close": [20.0],
        "future_ret": [float("nan")],
        "ret1": [0.015],
        "label": [float("nan")],
    })
    feature_repo.upsert_features(pd.concat([df1, df2], ignore_index=True))

    result = assemble_inference(feature_repo=feature_repo)

    dates = pd.to_datetime(result["date"]).dt.date
    assert dates.max() == date(2024, 1, 5)
    assert (dates == date(2024, 1, 5)).all()          # 只返回最新日截面
    assert set(result["code"]) == {"000002"}          # 含无标签最近行


def test_assemble_inference_raises_when_empty(conn):
    from src.features.assembler import assemble_inference

    feature_repo = FeatureRepo(conn)
    with pytest.raises(FileNotFoundError, match="features"):
        assemble_inference(feature_repo=feature_repo)
