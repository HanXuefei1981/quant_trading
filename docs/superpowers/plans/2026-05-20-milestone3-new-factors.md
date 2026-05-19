# Milestone 3 新因子扩展（第一批）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 A股 LightGBM 三分类模型新增机构覆盖、EPS 共识、龙虎榜信号三组因子，北向信号从 indicators.py 迁移到 signal.py。

**Architecture:** 新建 ReportCollector / SignalCollector 采集原始数据；新建 report.py / signal.py 计算特征；assembler.py 在特征工程步骤末尾调用两个新特征函数。北向特征（north_net_5d, north_net_trend）从 indicators.py 迁移到 signal.py，逻辑不变。

**Tech Stack:** Python 3.11, akshare, pandas, pytest, pathlib

---

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/collectors/report_collector.py` | 研报 + EPS 共识采集 |
| 新建 | `src/collectors/signal_collector.py` | 龙虎榜全市场日报采集 |
| 新建 | `src/features/report.py` | 研报因子计算（纯函数） |
| 新建 | `src/features/signal.py` | LHB + 北向信号因子计算（含北向迁移） |
| 新建 | `tests/test_report_collector.py` | ReportCollector 单元测试 |
| 新建 | `tests/test_signal_collector.py` | SignalCollector 单元测试 |
| 新建 | `tests/test_report_features.py` | 研报特征计算测试 |
| 新建 | `tests/test_signal_features.py` | 信号特征计算测试 |
| 修改 | `src/features/indicators.py` | 删除北向计算部分（第269-279行） |
| 修改 | `src/features/assembler.py` | 在特征工程后调用 add_report_features + add_signal_features |

---

## Task 1: ReportCollector — 研报分支

**Files:**
- Create: `src/collectors/report_collector.py`
- Create: `tests/test_report_collector.py`

### 数据说明

`ak.stock_research_report_em(symbol='000001')` 返回：

| 列名 | 含义 |
|------|------|
| `日期` | 研报发布日期 |
| `机构` | 发布机构名称 |
| `近一月个股研报数` | 近一月该股研报总数 |
| `报告名称` | 报告标题 |
| `东财评级` | 评级 |

落地路径：`data/raw/reports/{code}.parquet`
输出列：`date(datetime64), code(str), institution(str), rating(str), report_date(datetime64)`

- [ ] **Step 1: 写失败测试 — 文件落盘**

新建 `tests/test_report_collector.py`：

```python
"""测试 ReportCollector 研报分支"""
import sys
from pathlib import Path
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAKE_REPORT_DF = pd.DataFrame({
    "日期": ["2026-05-15", "2026-05-10"],
    "机构": ["国信证券", "招商证券"],
    "近一月个股研报数": [5, 5],
    "报告名称": ["点评报告A", "深度报告B"],
    "东财评级": ["买入", "中性"],
})


def test_fetch_one_report_saves_parquet(tmp_path):
    """fetch_one mode='report' 后 reports/{code}.parquet 存在且列齐全"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)

    with patch("akshare.stock_research_report_em", return_value=FAKE_REPORT_DF):
        result = collector.fetch_one("000001", mode="report")

    out = tmp_path / "reports" / "000001.parquet"
    assert out.exists()
    df = pd.read_parquet(out)
    assert set(["date", "code", "institution", "rating"]).issubset(df.columns)
    assert len(df) == 2
    assert result is not None


def test_fetch_one_returns_none_on_api_error(tmp_path):
    """API 抛异常时 fetch_one 返回 None，不抛出"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)

    with patch("akshare.stock_research_report_em", side_effect=Exception("network")):
        result = collector.fetch_one("000001", mode="report")

    assert result is None


def test_load_report_returns_dataframe(tmp_path):
    """load mode='report' 读缓存返回 DataFrame"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)
    df_saved = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-15"]),
        "code": ["000001"],
        "institution": ["国信证券"],
        "rating": ["买入"],
    })
    df_saved.to_parquet(reports_dir / "000001.parquet", index=False)

    result = collector.load("000001", mode="report")
    assert result is not None
    assert len(result) == 1


def test_load_returns_none_when_missing(tmp_path):
    """缓存不存在时 load 返回 None"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)
    assert collector.load("999999", mode="report") is None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd "/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading"
.venv/bin/pytest tests/test_report_collector.py -v 2>&1 | head -20
```

期望：`ModuleNotFoundError: No module named 'src.collectors.report_collector'`

- [ ] **Step 3: 实现研报分支**

新建 `src/collectors/report_collector.py`：

```python
"""研报 + EPS 共识采集器

数据落地路径：
  data/raw/reports/{code}.parquet  — 研报列表（date, code, institution, rating）
  data/raw/eps/{code}.parquet      — EPS 共识快照（snapshot_date, code, eps_cur, eps_next, analyst_count）
"""
import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional

import akshare as ak
import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats

logger = logging.getLogger(__name__)

_REPORT_COL_MAP = {
    "日期": "date",
    "机构": "institution",
    "东财评级": "rating",
}


class ReportCollector(BaseCollector):
    """研报列表 + EPS 共识采集器。

    mode='report': 东财研报列表 → data/raw/reports/{code}.parquet
    mode='eps':    同花顺 EPS 共识快照 → data/raw/eps/{code}.parquet
    """

    def __init__(self, base_dir: Optional[Path] = None, delay: float = 0.5) -> None:
        from config.settings import DATA_DIR
        self._base = base_dir or (DATA_DIR / "raw")
        self._reports_dir = self._base / "reports"
        self._eps_dir = self._base / "eps"
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        self._eps_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay

    # ── 研报 ──────────────────────────────────────────────────────────────────

    def _fetch_report(self, code: str) -> Optional[pd.DataFrame]:
        """拉取研报列表，返回标准化 DataFrame 或 None。"""
        try:
            raw = ak.stock_research_report_em(symbol=code)
        except Exception as exc:
            logger.debug(f"研报拉取失败 {code}: {exc}")
            return None

        if raw is None or raw.empty:
            return None

        cols_needed = [c for c in _REPORT_COL_MAP if c in raw.columns]
        df = raw[cols_needed].rename(columns=_REPORT_COL_MAP).copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["code"] = code
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        return df if not df.empty else None

    def _save_report(self, code: str, df: pd.DataFrame) -> None:
        path = self._reports_dir / f"{code}.parquet"
        if path.exists():
            existing = pd.read_parquet(path)
            existing["date"] = pd.to_datetime(existing["date"])
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=["date", "institution"]).sort_values("date")
        df.to_parquet(path, index=False)

    # ── EPS 共识 ──────────────────────────────────────────────────────────────

    def _fetch_eps(self, code: str) -> Optional[pd.DataFrame]:
        """拉取同花顺 EPS 共识快照，返回标准化 DataFrame 或 None。"""
        try:
            raw = ak.stock_profit_forecast_ths(
                symbol=code, indicator="预测年报每股收益"
            )
        except Exception as exc:
            logger.debug(f"EPS 共识拉取失败 {code}: {exc}")
            return None

        if raw is None or raw.empty:
            return None

        today = pd.Timestamp.today().normalize()
        rows = raw.sort_values("年度").reset_index(drop=True)

        eps_cur = rows["均值"].iloc[0] if len(rows) >= 1 else float("nan")
        eps_next = rows["均值"].iloc[1] if len(rows) >= 2 else float("nan")
        analyst_count = int(rows["预测机构数"].iloc[0]) if len(rows) >= 1 else 0

        return pd.DataFrame([{
            "snapshot_date": today,
            "code": code,
            "eps_cur": eps_cur,
            "eps_next": eps_next,
            "analyst_count": analyst_count,
        }])

    def _save_eps(self, code: str, df: pd.DataFrame) -> None:
        path = self._eps_dir / f"{code}.parquet"
        if path.exists():
            existing = pd.read_parquet(path)
            existing["snapshot_date"] = pd.to_datetime(existing["snapshot_date"])
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=["snapshot_date"]).sort_values("snapshot_date")
        df.to_parquet(path, index=False)

    # ── BaseCollector 接口 ────────────────────────────────────────────────────

    def fetch_one(
        self,
        code: str,
        mode: str = "report",
        since: Optional[date] = None,
    ) -> Optional[pd.DataFrame]:
        """拉取单只股票研报或 EPS 共识并落盘。"""
        if mode == "report":
            df = self._fetch_report(code)
            if df is not None:
                self._save_report(code, df)
            return df
        elif mode == "eps":
            df = self._fetch_eps(code)
            if df is not None:
                self._save_eps(code, df)
            return df
        else:
            raise ValueError(f"mode 必须是 'report' 或 'eps'，收到: {mode!r}")

    def fetch_all(
        self,
        codes: list[str],
        mode: str = "report",
        incremental: bool = True,
        max_errors: int = 50,
    ) -> CollectStats:
        """批量拉取，tqdm 进度，连续失败熔断。"""
        stats = CollectStats()
        consecutive_errors = 0
        cache_dir = self._reports_dir if mode == "report" else self._eps_dir

        for i, code in enumerate(tqdm(codes, desc=f"研报采集[{mode}]")):
            cache_path = cache_dir / f"{code}.parquet"
            if incremental and cache_path.exists():
                stats.cached += 1
                continue

            df = self.fetch_one(code, mode=mode)
            if df is not None:
                stats.ok += 1
                consecutive_errors = 0
            else:
                stats.fail += 1
                consecutive_errors += 1
                if consecutive_errors >= max_errors:
                    logger.error(f"连续 {max_errors} 次失败，终止采集")
                    break

            time.sleep(self.delay)

        logger.info(f"研报采集[{mode}]完成：{stats}")
        return stats

    def load(
        self,
        code: str,
        mode: str = "report",
    ) -> Optional[pd.DataFrame]:
        """从本地缓存加载，不触发网络请求。"""
        cache_dir = self._reports_dir if mode == "report" else self._eps_dir
        path = cache_dir / f"{code}.parquet"
        if not path.exists():
            return None
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            logger.warning(f"读取缓存失败 {code} [{mode}]: {exc}")
            return None
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/bin/pytest tests/test_report_collector.py::test_fetch_one_report_saves_parquet tests/test_report_collector.py::test_fetch_one_returns_none_on_api_error tests/test_report_collector.py::test_load_report_returns_dataframe tests/test_report_collector.py::test_load_returns_none_when_missing -v
```

期望：4 个 PASS

- [ ] **Step 5: 提交**

```bash
git add src/collectors/report_collector.py tests/test_report_collector.py
git commit -m "feat: ReportCollector 研报分支 (TDD)"
```

---

## Task 2: ReportCollector — EPS 分支测试

**Files:**
- Modify: `tests/test_report_collector.py`（追加 EPS 测试）

- [ ] **Step 1: 追加 EPS 测试用例**

在 `tests/test_report_collector.py` 末尾追加：

```python
FAKE_EPS_DF = pd.DataFrame({
    "年度": ["2026", "2027"],
    "预测机构数": [20, 18],
    "最小值": [2.08, 2.09],
    "均值": [2.17, 2.24],
    "最大值": [2.27, 2.36],
    "行业平均数": [1.89, 2.03],
})


def test_fetch_one_eps_saves_parquet(tmp_path):
    """fetch_one mode='eps' 后 eps/{code}.parquet 存在且列齐全"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)

    with patch("akshare.stock_profit_forecast_ths", return_value=FAKE_EPS_DF):
        result = collector.fetch_one("000001", mode="eps")

    out = tmp_path / "eps" / "000001.parquet"
    assert out.exists()
    df = pd.read_parquet(out)
    assert set(["snapshot_date", "code", "eps_cur", "eps_next", "analyst_count"]).issubset(df.columns)
    assert df["eps_cur"].iloc[0] == pytest.approx(2.17)
    assert df["analyst_count"].iloc[0] == 20
    assert result is not None


def test_fetch_one_eps_returns_none_on_api_error(tmp_path):
    """EPS API 失败时 fetch_one 返回 None"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)

    with patch("akshare.stock_profit_forecast_ths", side_effect=Exception("timeout")):
        result = collector.fetch_one("000001", mode="eps")

    assert result is None


def test_load_eps_returns_dataframe(tmp_path):
    """load mode='eps' 读缓存返回 DataFrame"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)
    eps_dir = tmp_path / "eps"
    eps_dir.mkdir(parents=True)
    df_saved = pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2026-05-19"]),
        "code": ["000001"],
        "eps_cur": [2.17],
        "eps_next": [2.24],
        "analyst_count": [20],
    })
    df_saved.to_parquet(eps_dir / "000001.parquet", index=False)

    result = collector.load("000001", mode="eps")
    assert result is not None
    assert result["eps_cur"].iloc[0] == pytest.approx(2.17)


def test_fetch_all_skips_cached_incremental(tmp_path):
    """incremental=True 时已有缓存的股票跳过，stats.cached 递增"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path, delay=0)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "000001.parquet").touch()  # 模拟已缓存

    with patch("akshare.stock_research_report_em", return_value=FAKE_REPORT_DF) as mock_api:
        stats = collector.fetch_all(["000001", "600036"], mode="report", incremental=True)

    assert stats.cached == 1
    assert mock_api.call_count == 1  # 只拉了 600036


def test_fetch_all_circuit_breaker(tmp_path):
    """连续失败达到 max_errors 后熔断，不再继续"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path, delay=0)
    codes = [f"{i:06d}" for i in range(20)]

    with patch("akshare.stock_research_report_em", side_effect=Exception("blocked")):
        stats = collector.fetch_all(codes, mode="report", incremental=False, max_errors=3)

    assert stats.fail == 3
    assert stats.ok == 0
```

- [ ] **Step 2: 运行全部测试**

```bash
.venv/bin/pytest tests/test_report_collector.py -v
```

期望：9 个 PASS

- [ ] **Step 3: 提交**

```bash
git add tests/test_report_collector.py
git commit -m "test: ReportCollector EPS 分支测试补全"
```

---

## Task 3: report.py — 研报特征函数

**Files:**
- Create: `src/features/report.py`
- Create: `tests/test_report_features.py`

### 功能说明

`add_report_features(df, code)` 从 `data/raw/reports/` 和 `data/raw/eps/` 加载数据，计算：
- `analyst_count`：历史已知覆盖机构数（每日前向填充）
- `report_count_30d`：近30日研报数（以 available_date = report_date + 1 计算）
- `eps_consensus_cur`：当年 EPS 均值（快照前向填充）
- `eps_revision`：EPS 修订方向（连续两次快照比较：+1 上调 / -1 下调 / 0 不变）

前视偏差：所有研报以 `available_date = date + 1天` 接入模型。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_report_features.py`：

```python
"""测试 report.py 研报特征计算"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_kline(dates: list[str]) -> pd.DataFrame:
    """构造最简 K 线 DataFrame"""
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "code": "000001",
        "close": [10.0] * len(dates),
    })


def test_report_count_30d_rolling(tmp_path):
    """近30日研报计数正确"""
    from src.features.report import add_report_features

    # 研报发布于 2026-05-01 和 2026-05-10
    reports = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-01", "2026-05-10"]),
        "code": ["000001", "000001"],
        "institution": ["国信证券", "招商证券"],
        "rating": ["买入", "中性"],
    })
    (tmp_path / "reports").mkdir(parents=True)
    reports.to_parquet(tmp_path / "reports" / "000001.parquet", index=False)
    (tmp_path / "eps").mkdir(parents=True)

    kline = _make_kline(["2026-05-12", "2026-05-13", "2026-06-15"])
    result = add_report_features(kline.copy(), "000001", base_dir=tmp_path)

    # 2026-05-12: available 5-02 和 5-11，窗口内2篇
    assert result.loc[result["date"] == pd.Timestamp("2026-05-12"), "report_count_30d"].iloc[0] == 2
    # 2026-06-15: 只有 5-11 的 available_date 5-11 在30日内
    assert result.loc[result["date"] == pd.Timestamp("2026-06-15"), "report_count_30d"].iloc[0] == 1


def test_no_lookahead_bias_report(tmp_path):
    """available_date = report_date + 1，当日发布的研报不出现在当日特征"""
    from src.features.report import add_report_features

    reports = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-15"]),
        "code": ["000001"],
        "institution": ["国信证券"],
        "rating": ["买入"],
    })
    (tmp_path / "reports").mkdir(parents=True)
    reports.to_parquet(tmp_path / "reports" / "000001.parquet", index=False)
    (tmp_path / "eps").mkdir(parents=True)

    # K 线日期 = 研报发布日（当日不应该能看到这篇研报）
    kline = _make_kline(["2026-05-15", "2026-05-16"])
    result = add_report_features(kline.copy(), "000001", base_dir=tmp_path)

    # 5-15 当日看不到（available_date = 5-16）
    assert result.loc[result["date"] == pd.Timestamp("2026-05-15"), "report_count_30d"].iloc[0] == 0
    # 5-16 可以看到
    assert result.loc[result["date"] == pd.Timestamp("2026-05-16"), "report_count_30d"].iloc[0] == 1


def test_missing_report_data_fills_nan(tmp_path):
    """无研报缓存时 report_count_30d 等全部为 NaN"""
    from src.features.report import add_report_features

    (tmp_path / "reports").mkdir(parents=True)
    (tmp_path / "eps").mkdir(parents=True)

    kline = _make_kline(["2026-05-15"])
    result = add_report_features(kline.copy(), "999999", base_dir=tmp_path)

    assert np.isnan(result["report_count_30d"].iloc[0])
    assert np.isnan(result["analyst_count"].iloc[0])


def test_eps_consensus_forward_filled(tmp_path):
    """EPS 快照前向填充到每日 K 线"""
    from src.features.report import add_report_features

    (tmp_path / "reports").mkdir(parents=True)
    eps_dir = tmp_path / "eps"
    eps_dir.mkdir(parents=True)
    eps_df = pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2026-04-01", "2026-05-01"]),
        "code": ["000001", "000001"],
        "eps_cur": [2.10, 2.17],
        "eps_next": [2.20, 2.24],
        "analyst_count": [18, 20],
    })
    eps_df.to_parquet(eps_dir / "000001.parquet", index=False)

    kline = _make_kline(["2026-04-15", "2026-05-10", "2026-06-01"])
    result = add_report_features(kline.copy(), "000001", base_dir=tmp_path)

    # 4-15: 使用 4-01 快照（前向填充）
    assert result.loc[result["date"] == pd.Timestamp("2026-04-15"), "eps_consensus_cur"].iloc[0] == pytest.approx(2.10)
    # 5-10: 使用 5-01 快照
    assert result.loc[result["date"] == pd.Timestamp("2026-05-10"), "eps_consensus_cur"].iloc[0] == pytest.approx(2.17)


def test_eps_revision_direction(tmp_path):
    """EPS 上调时 eps_revision = +1，下调时 = -1"""
    from src.features.report import add_report_features

    (tmp_path / "reports").mkdir(parents=True)
    eps_dir = tmp_path / "eps"
    eps_dir.mkdir(parents=True)
    eps_df = pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2026-04-01", "2026-05-01"]),
        "code": ["000001", "000001"],
        "eps_cur": [2.10, 2.17],   # 上调
        "eps_next": [2.20, 2.24],
        "analyst_count": [18, 20],
    })
    eps_df.to_parquet(eps_dir / "000001.parquet", index=False)

    kline = _make_kline(["2026-04-15", "2026-05-10"])
    result = add_report_features(kline.copy(), "000001", base_dir=tmp_path)

    # 4-01 快照没有前一期对比，revision = 0
    assert result.loc[result["date"] == pd.Timestamp("2026-04-15"), "eps_revision"].iloc[0] == 0
    # 5-01 快照相对 4-01 上调，revision = +1
    assert result.loc[result["date"] == pd.Timestamp("2026-05-10"), "eps_revision"].iloc[0] == 1


def test_missing_eps_data_fills_nan(tmp_path):
    """无 EPS 缓存时 eps_consensus_cur 为 NaN"""
    from src.features.report import add_report_features

    (tmp_path / "reports").mkdir(parents=True)
    (tmp_path / "eps").mkdir(parents=True)

    kline = _make_kline(["2026-05-15"])
    result = add_report_features(kline.copy(), "999999", base_dir=tmp_path)

    assert np.isnan(result["eps_consensus_cur"].iloc[0])
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/bin/pytest tests/test_report_features.py -v 2>&1 | head -15
```

期望：`ModuleNotFoundError: No module named 'src.features.report'`

- [ ] **Step 3: 实现 report.py**

新建 `src/features/report.py`：

```python
"""研报因子计算

输入：
  data/raw/reports/{code}.parquet — 研报列表（date, code, institution, rating）
  data/raw/eps/{code}.parquet     — EPS 共识快照（snapshot_date, code, eps_cur, eps_next, analyst_count）

输出（新增列）：
  analyst_count     — 覆盖机构数（前向填充）
  report_count_30d  — 近30日研报数（以 available_date = report_date + 1 计算）
  eps_consensus_cur — 当年 EPS 共识均值（快照前向填充）
  eps_revision      — EPS 修订方向（+1 上调 / -1 下调 / 0 不变）
"""
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_REPORT_WINDOW = 30  # 近30日研报计数窗口（自然日）


def _load_reports(code: str, base_dir: Path) -> Optional[pd.DataFrame]:
    path = base_dir / "reports" / f"{code}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception as exc:
        logger.debug(f"读取研报缓存失败 {code}: {exc}")
        return None


def _load_eps(code: str, base_dir: Path) -> Optional[pd.DataFrame]:
    path = base_dir / "eps" / f"{code}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
        return df.sort_values("snapshot_date").reset_index(drop=True)
    except Exception as exc:
        logger.debug(f"读取 EPS 缓存失败 {code}: {exc}")
        return None


def add_report_features(
    df: pd.DataFrame,
    code: str,
    base_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """左连接研报因子到单股 K 线，返回新 DataFrame（不修改原始）。

    前视偏差：available_date = report_date + 1 自然日。
    缺失数据：全部填 NaN，由预处理层填均值。
    """
    from config.settings import DATA_DIR
    base = base_dir or (DATA_DIR / "raw")

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])

    df = _add_report_counts(df, code, base)
    df = _add_eps_features(df, code, base)
    return df


def _add_report_counts(df: pd.DataFrame, code: str, base: Path) -> pd.DataFrame:
    """添加 analyst_count 和 report_count_30d。"""
    reports = _load_reports(code, base)
    if reports is None or reports.empty:
        df["analyst_count"] = np.nan
        df["report_count_30d"] = np.nan
        return df

    # available_date = report_date + 1 自然日（前视偏差控制）
    reports = reports.copy()
    reports["available_date"] = reports["date"] + pd.Timedelta(days=1)

    dates = df["date"].values

    report_counts = []
    analyst_counts = []
    for d in dates:
        d_ts = pd.Timestamp(d)
        window_start = d_ts - pd.Timedelta(days=_REPORT_WINDOW)
        # 仅使用 available_date <= 当前日期 的研报
        visible = reports[
            (reports["available_date"] <= d_ts)
            & (reports["available_date"] > window_start)
        ]
        report_counts.append(len(visible))
        analyst_counts.append(visible["institution"].nunique() if not visible.empty else 0)

    df["report_count_30d"] = report_counts
    df["analyst_count"] = analyst_counts
    return df


def _add_eps_features(df: pd.DataFrame, code: str, base: Path) -> pd.DataFrame:
    """添加 eps_consensus_cur 和 eps_revision。"""
    eps = _load_eps(code, base)
    if eps is None or eps.empty:
        df["eps_consensus_cur"] = np.nan
        df["eps_revision"] = np.nan
        return df

    # EPS 快照以 snapshot_date 作为 available_date（月频，不需要额外偏移）
    eps = eps.sort_values("snapshot_date").reset_index(drop=True)
    eps["eps_revision"] = np.sign(eps["eps_cur"].diff()).fillna(0).astype(int)

    # merge_asof：对每个 kline.date 取最近的 snapshot_date ≤ date（前向填充）
    df_sorted = df.sort_values("date").reset_index(drop=True)
    merged = pd.merge_asof(
        df_sorted,
        eps[["snapshot_date", "eps_cur", "eps_revision", "analyst_count"]].rename(
            columns={"snapshot_date": "date", "eps_cur": "_eps_cur",
                     "eps_revision": "_eps_revision", "analyst_count": "_analyst_count_eps"}
        ),
        on="date",
        direction="backward",
    )

    # analyst_count 优先使用研报计数（Task 3 已写入），EPS 的 analyst_count 备用
    if "analyst_count" not in df.columns or df["analyst_count"].isna().all():
        merged["analyst_count"] = merged.pop("_analyst_count_eps")
    else:
        merged = merged.drop(columns=["_analyst_count_eps"], errors="ignore")

    merged["eps_consensus_cur"] = merged.pop("_eps_cur")
    merged["eps_revision"] = merged.pop("_eps_revision")

    # 还原原始行序
    return merged.sort_values("date").reset_index(drop=True)
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/bin/pytest tests/test_report_features.py -v
```

期望：6 个 PASS

- [ ] **Step 5: 提交**

```bash
git add src/features/report.py tests/test_report_features.py
git commit -m "feat: report.py 研报因子计算 + 测试 (TDD)"
```

---

## Task 4: SignalCollector — 龙虎榜日报采集

**Files:**
- Create: `src/collectors/signal_collector.py`
- Create: `tests/test_signal_collector.py`

### 数据说明

`ak.stock_lhb_detail_em(start_date='YYYYMMDD', end_date='YYYYMMDD')` 返回所有上榜股票。
列名（中文）会映射到：`code`, `date`, `lhb_net_buy`, `lhb_buy_amount`, `lhb_sell_amount`。

⚠️ 该接口在 VPN 开启时有 SSL 问题。与 fund_flow 相同，需**关 VPN** 后运行。

落地路径：`data/raw/lhb/YYYY-MM-DD.parquet`，每日一个文件。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_signal_collector.py`：

```python
"""测试 SignalCollector 龙虎榜采集"""
import sys
from pathlib import Path
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAKE_LHB_DF = pd.DataFrame({
    "代码": ["000001", "600036"],
    "名称": ["平安银行", "招商银行"],
    "上榜日期": ["2026-05-16", "2026-05-16"],
    "收盘价": [12.5, 38.2],
    "涨跌幅": [3.5, 2.1],
    "龙虎榜净买额": [5000000.0, -2000000.0],
    "买入额合计": [8000000.0, 3000000.0],
    "卖出额合计": [3000000.0, 5000000.0],
})


def test_fetch_all_saves_daily_parquet(tmp_path):
    """fetch_all 后 lhb/2026-05-16.parquet 存在"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)

    with patch("akshare.stock_lhb_detail_em", return_value=FAKE_LHB_DF):
        stats = collector.fetch_all(
            codes=[], date=datetime.date(2026, 5, 16), incremental=False
        )

    out = tmp_path / "lhb" / "2026-05-16.parquet"
    assert out.exists()
    df = pd.read_parquet(out)
    assert set(["date", "code", "lhb_net_buy", "lhb_buy_amount", "lhb_sell_amount"]).issubset(df.columns)
    assert len(df) == 2
    assert stats.ok == 1


def test_fetch_all_correct_filename(tmp_path):
    """文件名格式为 YYYY-MM-DD.parquet"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)

    with patch("akshare.stock_lhb_detail_em", return_value=FAKE_LHB_DF):
        collector.fetch_all(codes=[], date=datetime.date(2026, 5, 9), incremental=False)

    assert (tmp_path / "lhb" / "2026-05-09.parquet").exists()


def test_fetch_all_dedup_same_date(tmp_path):
    """同日重复拉取不产生重复行（drop_duplicates by code + date）"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)

    with patch("akshare.stock_lhb_detail_em", return_value=FAKE_LHB_DF):
        collector.fetch_all(codes=[], date=datetime.date(2026, 5, 16), incremental=False)
        collector.fetch_all(codes=[], date=datetime.date(2026, 5, 16), incremental=False)

    df = pd.read_parquet(tmp_path / "lhb" / "2026-05-16.parquet")
    assert len(df) == 2  # 没有重复


def test_fetch_all_incremental_skips_existing(tmp_path):
    """incremental=True 时已有该日文件则跳过"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)
    lhb_dir = tmp_path / "lhb"
    lhb_dir.mkdir(parents=True)
    (lhb_dir / "2026-05-16.parquet").touch()

    with patch("akshare.stock_lhb_detail_em", return_value=FAKE_LHB_DF) as mock_api:
        collector.fetch_all(codes=[], date=datetime.date(2026, 5, 16), incremental=True)

    mock_api.assert_not_called()


def test_load_aggregates_across_dates(tmp_path):
    """load(code) 聚合多日文件，返回该股所有上榜记录"""
    from src.collectors.signal_collector import SignalCollector

    collector = SignalCollector(base_dir=tmp_path)
    lhb_dir = tmp_path / "lhb"
    lhb_dir.mkdir(parents=True)

    for date_str, net_buy in [("2026-05-15", 1000000.0), ("2026-05-16", 2000000.0)]:
        df = pd.DataFrame({
            "date": pd.to_datetime([date_str]),
            "code": ["000001"],
            "lhb_net_buy": [net_buy],
            "lhb_buy_amount": [3000000.0],
            "lhb_sell_amount": [1000000.0],
        })
        df.to_parquet(lhb_dir / f"{date_str}.parquet", index=False)

    result = collector.load("000001")
    assert result is not None
    assert len(result) == 2
    assert set(result["lhb_net_buy"]) == {1000000.0, 2000000.0}


def test_load_returns_none_when_no_data(tmp_path):
    """该股从未上榜时 load 返回 None"""
    from src.collectors.signal_collector import SignalCollector

    collector = SignalCollector(base_dir=tmp_path)
    (tmp_path / "lhb").mkdir(parents=True)

    assert collector.load("999999") is None


def test_load_market_reads_single_date(tmp_path):
    """load_market(date) 读取指定日期的全市场文件"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)
    lhb_dir = tmp_path / "lhb"
    lhb_dir.mkdir(parents=True)
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-16"]),
        "code": ["000001"],
        "lhb_net_buy": [1000000.0],
        "lhb_buy_amount": [2000000.0],
        "lhb_sell_amount": [1000000.0],
    })
    df.to_parquet(lhb_dir / "2026-05-16.parquet", index=False)

    result = collector.load_market(datetime.date(2026, 5, 16))
    assert result is not None
    assert len(result) == 1


def test_load_market_returns_none_when_missing(tmp_path):
    """指定日期无文件时 load_market 返回 None"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)
    (tmp_path / "lhb").mkdir(parents=True)

    assert collector.load_market(datetime.date(2026, 5, 16)) is None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/bin/pytest tests/test_signal_collector.py -v 2>&1 | head -10
```

期望：`ModuleNotFoundError`

- [ ] **Step 3: 实现 signal_collector.py**

新建 `src/collectors/signal_collector.py`：

```python
"""龙虎榜全市场日报采集器

数据源：akshare.stock_lhb_detail_em(start_date, end_date)（东财，需关 VPN）
落地路径：data/raw/lhb/YYYY-MM-DD.parquet

列格式：date(datetime64), code(str), lhb_net_buy(float), lhb_buy_amount(float), lhb_sell_amount(float)
"""
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import akshare as ak
import pandas as pd

from src.collectors.base import BaseCollector, CollectStats

logger = logging.getLogger(__name__)

_COL_MAP = {
    "代码": "code",
    "股票代码": "code",
    "上榜日期": "date",
    "龙虎榜净买额": "lhb_net_buy",
    "买入额合计": "lhb_buy_amount",
    "卖出额合计": "lhb_sell_amount",
}


class SignalCollector(BaseCollector):
    """龙虎榜全市场日报采集器（按日期粒度，非按股票粒度）。

    ⚠️ akshare.stock_lhb_detail_em 在 VPN 开启时有 SSL 错误，需关 VPN 后运行。
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        from config.settings import DATA_DIR
        self._base = base_dir or (DATA_DIR / "raw")
        self._lhb_dir = self._base / "lhb"
        self._lhb_dir.mkdir(parents=True, exist_ok=True)

    def _fetch_daily(self, target_date: date) -> Optional[pd.DataFrame]:
        """拉取单日全市场龙虎榜，返回标准化 DataFrame 或 None。"""
        date_str = target_date.strftime("%Y%m%d")
        try:
            raw = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        except Exception as exc:
            logger.warning(f"龙虎榜拉取失败 {date_str}: {exc}")
            return None

        if raw is None or raw.empty:
            return None

        cols_to_rename = {k: v for k, v in _COL_MAP.items() if k in raw.columns}
        df = raw.rename(columns=cols_to_rename)

        required = {"code", "lhb_net_buy"}
        if not required.issubset(df.columns):
            logger.warning(f"龙虎榜字段不全 {date_str}，已有列: {list(df.columns)}")
            return None

        df["date"] = pd.to_datetime(target_date)
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["lhb_net_buy"] = pd.to_numeric(df["lhb_net_buy"], errors="coerce")
        df["lhb_buy_amount"] = pd.to_numeric(df.get("lhb_buy_amount", 0), errors="coerce")
        df["lhb_sell_amount"] = pd.to_numeric(df.get("lhb_sell_amount", 0), errors="coerce")

        keep = ["date", "code", "lhb_net_buy", "lhb_buy_amount", "lhb_sell_amount"]
        df = df[[c for c in keep if c in df.columns]]
        df = df.dropna(subset=["code"]).drop_duplicates(subset=["code"])
        return df if not df.empty else None

    def fetch_all(
        self,
        codes: list[str],
        date: Optional[date] = None,
        incremental: bool = True,
        max_errors: int = 10,
    ) -> CollectStats:
        """拉取指定日期（默认今日）的全市场龙虎榜并落盘。codes 参数忽略。"""
        stats = CollectStats()
        target = date or datetime.today().date()
        out_path = self._lhb_dir / f"{target.strftime('%Y-%m-%d')}.parquet"

        if incremental and out_path.exists():
            stats.cached += 1
            return stats

        df = self._fetch_daily(target)
        if df is None:
            stats.fail += 1
            return stats

        if out_path.exists():
            existing = pd.read_parquet(out_path)
            existing["date"] = pd.to_datetime(existing["date"])
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=["date", "code"]).sort_values("date")

        df.to_parquet(out_path, index=False)
        stats.ok += 1
        logger.info(f"龙虎榜 {target} 已保存：{len(df)} 只股票")
        return stats

    def fetch_one(self, code: str, since: Optional[date] = None) -> Optional[pd.DataFrame]:
        """为满足 BaseCollector 接口，等同于拉取今日并返回该股记录。"""
        target = datetime.today().date()
        self.fetch_all(codes=[], date=target, incremental=True)
        return self.load(code)

    def load(self, code: str) -> Optional[pd.DataFrame]:
        """从所有历史日期文件中聚合该股上榜记录。"""
        parts = []
        for p in sorted(self._lhb_dir.glob("*.parquet")):
            try:
                df = pd.read_parquet(p)
                df["date"] = pd.to_datetime(df["date"])
                sub = df[df["code"] == code]
                if not sub.empty:
                    parts.append(sub)
            except Exception:
                continue
        if not parts:
            return None
        result = pd.concat(parts, ignore_index=True).sort_values("date").reset_index(drop=True)
        return result if not result.empty else None

    def load_market(self, date: Optional[date] = None) -> Optional[pd.DataFrame]:
        """读取单日全市场龙虎榜文件。"""
        target = date or datetime.today().date()
        path = self._lhb_dir / f"{target.strftime('%Y-%m-%d')}.parquet"
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception as exc:
            logger.warning(f"读取龙虎榜 {target} 失败: {exc}")
            return None
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/bin/pytest tests/test_signal_collector.py -v
```

期望：8 个 PASS

- [ ] **Step 5: 提交**

```bash
git add src/collectors/signal_collector.py tests/test_signal_collector.py
git commit -m "feat: SignalCollector 龙虎榜日报采集 (TDD)"
```

---

## Task 5: signal.py — 信号特征函数 + 北向迁移

**Files:**
- Create: `src/features/signal.py`
- Create: `tests/test_signal_features.py`
- Modify: `src/features/indicators.py`（删除北向计算段）

### 功能说明

`add_signal_features(df, code, lhb_df, north_df)` 计算：
- `lhb_net_buy_30d`：近30日龙虎榜净买入（元）
- `lhb_count_30d`：近30日上榜次数
- `north_net_5d`：近5日累计北向净买入（迁移自 indicators.py）
- `north_net_trend`：北向资金动量（迁移自 indicators.py）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_signal_features.py`：

```python
"""测试 signal.py 信号特征计算（龙虎榜 + 北向迁移）"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_kline(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "code": "000001",
        "close": [10.0] * len(dates),
    })


def _make_lhb(date: str, code: str, net_buy: float) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime([date]),
        "code": [code],
        "lhb_net_buy": [net_buy],
        "lhb_buy_amount": [abs(net_buy) * 1.5],
        "lhb_sell_amount": [abs(net_buy) * 0.5],
    })


def test_lhb_count_30d_rolling(tmp_path):
    """近30日上榜次数滚动正确（含前视偏差 +1 天）"""
    from src.features.signal import add_signal_features

    lhb_df = pd.concat([
        _make_lhb("2026-05-10", "000001", 1e6),
        _make_lhb("2026-05-15", "000001", 2e6),
    ], ignore_index=True)

    kline = _make_kline(["2026-05-11", "2026-05-16", "2026-06-20"])
    result = add_signal_features(kline.copy(), "000001", lhb_df=lhb_df, north_df=None)

    # 5-11: available 5-11（5-10+1），窗口内1次
    assert result.loc[result["date"] == pd.Timestamp("2026-05-11"), "lhb_count_30d"].iloc[0] == 1
    # 5-16: available 5-11 和 5-16，窗口内2次
    assert result.loc[result["date"] == pd.Timestamp("2026-05-16"), "lhb_count_30d"].iloc[0] == 2
    # 6-20: 只有 5-16 的 available_date 5-16 在30日内
    assert result.loc[result["date"] == pd.Timestamp("2026-06-20"), "lhb_count_30d"].iloc[0] == 1


def test_lhb_net_buy_30d_rolling(tmp_path):
    """近30日净买入累计正确"""
    from src.features.signal import add_signal_features

    lhb_df = pd.concat([
        _make_lhb("2026-05-10", "000001", 1e6),
        _make_lhb("2026-05-15", "000001", 3e6),
    ], ignore_index=True)

    kline = _make_kline(["2026-05-16"])
    result = add_signal_features(kline.copy(), "000001", lhb_df=lhb_df, north_df=None)

    # available 5-11 和 5-16，窗口内净买入 = 1e6 + 3e6
    val = result.loc[result["date"] == pd.Timestamp("2026-05-16"), "lhb_net_buy_30d"].iloc[0]
    assert val == pytest.approx(4e6)


def test_no_lookahead_bias_lhb():
    """龙虎榜 available_date = lhb_date + 1，当日数据不出现在当日特征"""
    from src.features.signal import add_signal_features

    lhb_df = _make_lhb("2026-05-15", "000001", 5e6)
    kline = _make_kline(["2026-05-15", "2026-05-16"])
    result = add_signal_features(kline.copy(), "000001", lhb_df=lhb_df, north_df=None)

    # 5-15 当日看不到该上榜记录
    assert result.loc[result["date"] == pd.Timestamp("2026-05-15"), "lhb_count_30d"].iloc[0] == 0
    # 5-16 可以看到
    assert result.loc[result["date"] == pd.Timestamp("2026-05-16"), "lhb_count_30d"].iloc[0] == 1


def test_north_signal_migrated_values():
    """north_net_5d / north_net_trend 数值与迁移前 indicators.py 逻辑完全一致"""
    from src.features.signal import add_signal_features

    # 构造10天北向数据
    dates = pd.date_range("2026-05-01", periods=10)
    north_vals = [1e8, -2e8, 3e8, 1e8, 2e8, -1e8, 4e8, 2e8, -3e8, 1e8]
    north_df = pd.DataFrame({"date": dates, "north_net_inflow": north_vals})

    kline = pd.DataFrame({"date": dates, "code": "000001", "close": [10.0] * 10})
    result = add_signal_features(kline.copy(), "000001", lhb_df=None, north_df=north_df)

    assert "north_net_5d" in result.columns
    assert "north_net_trend" in result.columns

    # 手动计算验证最后一行
    series = pd.Series(north_vals)
    expected_5d = series.rolling(5, min_periods=1).sum().iloc[-1]
    assert result["north_net_5d"].iloc[-1] == pytest.approx(expected_5d)


def test_missing_lhb_fills_nan():
    """无龙虎榜数据时 lhb_* 全部为 NaN"""
    from src.features.signal import add_signal_features

    kline = _make_kline(["2026-05-15"])
    result = add_signal_features(kline.copy(), "000001", lhb_df=None, north_df=None)

    assert np.isnan(result["lhb_count_30d"].iloc[0])
    assert np.isnan(result["lhb_net_buy_30d"].iloc[0])


def test_missing_north_fills_nan():
    """无北向数据时 north_* 全部为 NaN"""
    from src.features.signal import add_signal_features

    kline = _make_kline(["2026-05-15"])
    result = add_signal_features(kline.copy(), "000001", lhb_df=None, north_df=None)

    assert np.isnan(result["north_net_5d"].iloc[0])
    assert np.isnan(result["north_net_trend"].iloc[0])
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/bin/pytest tests/test_signal_features.py -v 2>&1 | head -10
```

期望：`ModuleNotFoundError`

- [ ] **Step 3: 实现 signal.py**

新建 `src/features/signal.py`：

```python
"""信号因子计算：龙虎榜 + 北向资金

北向信号（north_net_5d, north_net_trend）从 indicators.py 迁移而来，逻辑完全不变。
龙虎榜信号（lhb_net_buy_30d, lhb_count_30d）为新增因子。

前视偏差：龙虎榜 available_date = lhb_date + 1 自然日。
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_LHB_WINDOW = 30   # 近30日龙虎榜滚动窗口（自然日）


def add_signal_features(
    df: pd.DataFrame,
    code: str,
    lhb_df: Optional[pd.DataFrame],
    north_df: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """左连接信号因子到单股 K 线，返回新 DataFrame（不修改原始）。

    Args:
        df:       单股 K 线 DataFrame，含 date 列
        code:     股票代码，用于从 lhb_df 过滤
        lhb_df:   全市场龙虎榜历史（所有日期合并），含 date, code, lhb_net_buy 等
        north_df: 北向资金历史，含 date, north_net_inflow

    Returns:
        新 DataFrame，新增 lhb_net_buy_30d, lhb_count_30d, north_net_5d, north_net_trend
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = _add_lhb_features(df, code, lhb_df)
    df = _add_north_features(df, north_df)
    return df


def _add_lhb_features(
    df: pd.DataFrame,
    code: str,
    lhb_df: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """添加龙虎榜相关因子。"""
    if lhb_df is None or lhb_df.empty:
        df["lhb_net_buy_30d"] = np.nan
        df["lhb_count_30d"] = np.nan
        return df

    # 过滤该股上榜记录，计算 available_date = lhb_date + 1
    stock_lhb = lhb_df[lhb_df["code"] == code].copy()
    if stock_lhb.empty:
        df["lhb_net_buy_30d"] = np.nan
        df["lhb_count_30d"] = np.nan
        return df

    stock_lhb["date"] = pd.to_datetime(stock_lhb["date"])
    stock_lhb["available_date"] = stock_lhb["date"] + pd.Timedelta(days=1)

    dates = df["date"].values
    net_buy_30d = []
    count_30d = []

    for d in dates:
        d_ts = pd.Timestamp(d)
        window_start = d_ts - pd.Timedelta(days=_LHB_WINDOW)
        visible = stock_lhb[
            (stock_lhb["available_date"] <= d_ts)
            & (stock_lhb["available_date"] > window_start)
        ]
        net_buy_30d.append(visible["lhb_net_buy"].sum() if not visible.empty else np.nan)
        count_30d.append(len(visible))

    df["lhb_net_buy_30d"] = net_buy_30d
    df["lhb_count_30d"] = count_30d
    # 未上榜时 count_30d=0 是有意义的，净买入为 NaN 时补 0
    df["lhb_net_buy_30d"] = df["lhb_net_buy_30d"].fillna(0.0)
    return df


def _add_north_features(
    df: pd.DataFrame,
    north_df: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """添加北向资金因子（逻辑迁移自 indicators._add_fund_flow_features）。"""
    if north_df is not None and not north_df.empty:
        north_slim = north_df[["date", "north_net_inflow"]].copy()
        north_slim["date"] = pd.to_datetime(north_slim["date"])
        df = df.merge(north_slim, on="date", how="left")

    if "north_net_inflow" in df.columns:
        df["north_net_5d"] = df["north_net_inflow"].rolling(5, min_periods=1).sum()
        ma5 = df["north_net_inflow"].rolling(5, min_periods=1).mean()
        ma20 = df["north_net_inflow"].rolling(20, min_periods=5).mean()
        df["north_net_trend"] = ma5 / (ma20.abs() + 1e-6)
    else:
        df["north_net_5d"] = np.nan
        df["north_net_trend"] = np.nan

    return df
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/bin/pytest tests/test_signal_features.py -v
```

期望：6 个 PASS

- [ ] **Step 5: 删除 indicators.py 北向计算段**

编辑 `src/features/indicators.py`，找到 `_add_fund_flow_features` 函数（约第241行），**删除北向部分**（第269-279行）：

删除这段：
```python
    # --- 北向资金（宏观环境，市场统一值，按日期 join 进来）---
    if "north_net_inflow" in df.columns:
        # 近5日累计北向净买入（亿元）
        df["north_net_5d"] = df["north_net_inflow"].rolling(5, min_periods=1).sum()
        # 北向资金动量：近5日均值 / 近20日均值绝对值（>1 说明加速买入）
        ma5 = df["north_net_inflow"].rolling(5, min_periods=1).mean()
        ma20 = df["north_net_inflow"].rolling(20, min_periods=5).mean()
        df["north_net_trend"] = ma5 / (ma20.abs() + 1e-6)
    else:
        df["north_net_5d"] = np.nan
        df["north_net_trend"] = np.nan
```

- [ ] **Step 6: 运行全部相关测试确认无回归**

```bash
.venv/bin/pytest tests/test_signal_features.py tests/test_indicators.py -v
```

期望：全部 PASS（north_net_* 相关 indicator 测试若存在需更新，否则直接通过）

- [ ] **Step 7: 提交**

```bash
git add src/features/signal.py tests/test_signal_features.py src/features/indicators.py
git commit -m "feat: signal.py 信号因子 + 北向迁移 + indicators.py 清理 (TDD)"
```

---

## Task 6: assembler.py 集成

**Files:**
- Modify: `src/features/assembler.py`（新增 _load_lhb_all，更新特征计算调用链）

### 改动说明

在 `assemble()` 和 `assemble_incremental()` 中：
1. 新增 `_load_lhb_all()` — 一次性加载所有历史龙虎榜数据
2. 特征循环末尾增加调用：`add_report_features(df, code)` 和 `add_signal_features(df, code, lhb_df, north_df)`
3. `_merge_northbound` 调用保留（north_df 仍传入 add_signal_features）

- [ ] **Step 1: 在 assembler.py 顶部添加新导入**

在 `src/features/assembler.py` 现有 import 块末尾（约第27行之后）追加：

```python
from src.features.report import add_report_features
from src.features.signal import add_signal_features

LHB_DIR = DATA_DIR / "raw" / "lhb"
REPORT_DIR = DATA_DIR / "raw" / "reports"
EPS_DIR = DATA_DIR / "raw" / "eps"
```

- [ ] **Step 2: 新增 _load_lhb_all 函数**

在 `_load_northbound` 函数之后（约第73行）插入：

```python
def _load_lhb_all() -> Optional[pd.DataFrame]:
    """加载所有历史龙虎榜日报并合并为一张宽表（data/raw/lhb/*.parquet）。"""
    if not LHB_DIR.exists():
        return None
    parts = []
    for p in sorted(LHB_DIR.glob("*.parquet")):
        try:
            df = pd.read_parquet(p)
            df["date"] = pd.to_datetime(df["date"])
            parts.append(df)
        except Exception:
            continue
    if not parts:
        return None
    combined = pd.concat(parts, ignore_index=True)
    combined = combined.drop_duplicates(subset=["date", "code"]).sort_values("date")
    return combined
```

- [ ] **Step 3: 更新 assemble() 的特征工程段**

在 `assemble()` 函数中，找到一次性加载北向数据的行（约第150行）：

```python
    north_df = _load_northbound()
```

在其后追加：

```python
    lhb_df = _load_lhb_all()
    if lhb_df is not None:
        logger.info(f"龙虎榜历史已加载：{len(lhb_df)} 条，覆盖 {lhb_df['code'].nunique()} 只股票")
    else:
        logger.info("龙虎榜数据未找到，lhb_* 因子将全部为 NaN/0")
```

然后找到特征工程循环内（约第203-207行）：

```python
        # 特征工程
        df = add_all_features(raw)
        df["code"] = code
        df.to_parquet(processed_path, index=False)
```

修改为：

```python
        # 特征工程
        df = add_all_features(raw)
        df = add_report_features(df, code)
        df = add_signal_features(df, code, lhb_df=lhb_df, north_df=north_df)
        df["code"] = code
        df.to_parquet(processed_path, index=False)
```

- [ ] **Step 4: 更新 assemble_incremental() 的对应段**

在 `assemble_incremental()` 中找到类似的 `north_df = _load_northbound()` 调用，在其后追加：

```python
    lhb_df = _load_lhb_all()
```

然后找到增量模式的特征工程段（类似结构），同样追加：

```python
        df = add_report_features(df, code)
        df = add_signal_features(df, code, lhb_df=lhb_df, north_df=north_df)
```

- [ ] **Step 5: 更新 get_feature_columns 排除列表**

在 `indicators.py::get_feature_columns` 的 `exclude` 集合（约第296行）中，**移除** `"north_net_inflow"` 外的北向原始列（已无意义的原始列），并确认 `north_net_inflow` 保留在排除列表（因为它是 raw 列，不入模型）：

```python
    exclude = {
        ...
        # 资金流向原始值（衍生特征才入模型）
        "major_net_inflow", "major_net_pct", "north_net_inflow",
        # LHB 原始值（衍生特征才入模型）
        "lhb_buy_amount", "lhb_sell_amount",
    }
```

- [ ] **Step 6: 快速冒烟测试（5只股票）**

```bash
cd "/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading"
.venv/bin/python -c "
from src.features.assembler import assemble
df = assemble(sample_size=5)
new_cols = ['report_count_30d', 'analyst_count', 'eps_consensus_cur',
            'eps_revision', 'lhb_net_buy_30d', 'lhb_count_30d',
            'north_net_5d', 'north_net_trend']
for c in new_cols:
    print(f'{c}: {\"OK\" if c in df.columns else \"MISSING\"}, non-null={df[c].notna().sum()}')
"
```

期望：8 列全部 OK（非空数量可以为 0，但列必须存在）

- [ ] **Step 7: 运行完整测试套件**

```bash
.venv/bin/pytest tests/ -v --ignore=tests/test_engine.py 2>&1 | tail -20
```

期望：无新增 FAIL

- [ ] **Step 8: 提交**

```bash
git add src/features/assembler.py src/features/indicators.py
git commit -m "feat: assembler 集成 report/signal 特征 + get_feature_columns 更新"
```

---

## Task 7: IC 验收与对比

此任务为手动执行，验证新因子是否满足验收标准。

- [ ] **Step 1: 全量重跑 Phase 1**

```bash
.venv/bin/python main.py 1
```

完成后确认 `data/processed/market_features.parquet` 包含新列：

```bash
.venv/bin/python -c "
import pandas as pd
df = pd.read_parquet('data/processed/market_features.parquet')
new_cols = ['report_count_30d', 'analyst_count', 'eps_consensus_cur',
            'eps_revision', 'lhb_net_buy_30d', 'lhb_count_30d',
            'north_net_5d', 'north_net_trend']
for c in new_cols:
    pct = df[c].notna().mean() * 100
    print(f'{c}: {pct:.1f}% non-null')
"
```

- [ ] **Step 2: 重跑 Phase 2（rolling 模式）**

```bash
.venv/bin/python main.py 2 --rolling
```

- [ ] **Step 3: 对比 IC**

```bash
.venv/bin/python -c "
import json
with open('data/models/eval_results.json') as f:
    r = json.load(f)
print('训练集 IC:', r.get('train_ic'))
print('验证集 IC:', r.get('val_ic'))
print('测试集 IC:', r.get('test_ic'))
print()
print('基准（加因子前）— 测试集 IC: 0.0197')
print('目标 >= 0.0197 * 1.05 =', round(0.0197 * 1.05, 4))
"
```

期望：测试集 IC ≥ 0.0207

- [ ] **Step 4: 提交验收结果到 dev-log**

在 `docs/dev-log/` 新建当日日志，记录 IC 对比结果和 Top-10 扫描输出。

```bash
.venv/bin/python main.py scan
```

---

## 执行时序总结

```
Day 1: Task 1 (ReportCollector 研报) + Task 2 (EPS 分支)
Day 2: Task 3 (report.py 特征)
Day 3: Task 4 (SignalCollector 龙虎榜)
Day 4: Task 5 (signal.py + 北向迁移)
Day 5: Task 6 (assembler 集成) + Task 7 (IC 验收)
```
