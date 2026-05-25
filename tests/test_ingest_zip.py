import struct, zipfile, io, tempfile
from pathlib import Path
import duckdb, pandas as pd, pytest, sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.data.ingest_zip import ingest_kline


def _make_day_bytes(records: list[tuple]) -> bytes:
    """records: [(date_int, open_i, high_i, low_i, close_i, amount_f, volume_i)]"""
    buf = bytearray()
    for d, o, h, l, c, a, v in records:
        buf += struct.pack('<IIIIIfII', d, o, h, l, c, a, v, 0)
    return bytes(buf)


@pytest.fixture
def fake_zip(tmp_path):
    """构造只含 2 只股票 (sh000001, sz000002) 各 3 条记录的测试 zip。"""
    sh_records = [
        (20240102, 300, 310, 295, 305, 1.5e9, 5000),
        (20240103, 305, 315, 300, 312, 1.6e9, 5200),
        (20240104, 312, 320, 308, 318, 1.7e9, 5400),
    ]
    sz_records = [
        (20240102, 100, 105, 98, 103, 5e8, 3000),
        (20240103, 103, 108, 100, 106, 5.2e8, 3100),
        (20240104, 106, 110, 104, 108, 5.4e8, 3200),
    ]
    zip_path = tmp_path / "test_hsjday.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("sh/lday/sh000001.day", _make_day_bytes(sh_records))
        zf.writestr("sz/lday/sz000002.day", _make_day_bytes(sz_records))
    return zip_path


def test_ingest_kline_writes_all_rows(fake_zip):
    conn = duckdb.connect(":memory:")
    migrate(conn)
    raw_repo = RawRepo(conn)

    stats = ingest_kline(fake_zip, raw_repo)

    assert stats.ok == 2          # 2 只股票
    assert stats.fail == 0

    df1 = raw_repo.load_kline("000001")
    assert len(df1) == 3
    assert abs(df1.iloc[0]["close"] - 3.05) < 0.01   # 305/100

    df2 = raw_repo.load_kline("000002")
    assert len(df2) == 3


def test_ingest_kline_filters_zero_close(tmp_path):
    """zero-close 记录应被过滤。"""
    bad_records = [
        (20240102, 0, 0, 0, 0, 0.0, 0),   # 全零异常
        (20240103, 100, 105, 98, 103, 5e8, 3000),
    ]
    zip2 = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip2, 'w') as zf:
        zf.writestr("sz/lday/sz999999.day", _make_day_bytes(bad_records))

    conn = duckdb.connect(":memory:")
    migrate(conn)
    raw_repo = RawRepo(conn)
    ingest_kline(zip2, raw_repo)

    df = raw_repo.load_kline("999999")
    assert len(df) == 1   # 只有 1 条有效记录


def test_ingest_kline_date_range(fake_zip):
    """START_DATE 过滤：只保留 >= 20210101 的记录（这里全部保留）。"""
    conn = duckdb.connect(":memory:")
    migrate(conn)
    raw_repo = RawRepo(conn)
    stats = ingest_kline(fake_zip, raw_repo)

    df = raw_repo.load_kline("000001")
    assert all(str(d)[:10] >= "2024-01-01" for d in df["date"])


def test_ingest_kline_windows_backslash_path(tmp_path):
    """Windows zip 路径（反斜杠分隔）应被正确解析。"""
    records = [
        (20240102, 100, 105, 98, 103, 5e8, 3000),
    ]
    zip_path = tmp_path / "windows.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr(r"sh\lday\sh123456.day", _make_day_bytes(records))

    conn = duckdb.connect(":memory:")
    migrate(conn)
    raw_repo = RawRepo(conn)
    stats = ingest_kline(zip_path, raw_repo)

    assert stats.ok == 1
    df = raw_repo.load_kline("123456")
    assert len(df) == 1
