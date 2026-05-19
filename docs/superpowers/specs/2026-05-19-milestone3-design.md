# Milestone 3 设计文档：新因子扩展（第一批）

**日期：** 2026-05-19  
**状态：** 已审批，待实现  
**依赖：** M1（架构解耦）✅ M2（增量更新）✅

---

## 一、目标

在现有 LightGBM 三分类模型基础上，新增第一批四组因子：

| 因子组 | 具体因子 | 数据源 | 更新频率 |
|--------|---------|--------|---------|
| 机构覆盖 | `analyst_count`, `report_count_30d` | 东财研报列表 `stock_report_em()` | 周频 |
| EPS 共识 | `eps_consensus_cur`, `eps_revision` | akshare THS `stock_analyst_forecast_ths()` | 月频 |
| 龙虎榜信号 | `lhb_net_buy_30d`, `lhb_count_30d` | 东财龙虎榜 `stock_lhb_detail_em()` | 日频 |
| 北向信号 | `north_net_5d`, `north_net_trend` | 现有 `northbound_collector`（迁移自 indicators.py） | 日频 |

**验收标准：**
- 每个新因子 IC 绝对值 > 0.02
- 方向符合业务逻辑（机构覆盖↑→正向，龙虎榜净买↑→正向）
- 加入新因子后模型 IC ≥ 基准 × 1.05

---

## 二、新增文件

| 文件 | 职责 |
|------|------|
| `src/collectors/report_collector.py` | 研报 + EPS 共识采集，落地到 `data/raw/reports/` 和 `data/raw/eps/` |
| `src/collectors/signal_collector.py` | 龙虎榜全市场日报采集，落地到 `data/raw/lhb/` |
| `src/features/report.py` | 研报因子计算（纯函数，不触网络） |
| `src/features/signal.py` | 龙虎榜 + 北向信号因子计算（从 `indicators.py` 迁移北向部分） |
| `tests/test_report_collector.py` | ReportCollector 单元测试（≥10 用例） |
| `tests/test_signal_collector.py` | SignalCollector 单元测试（≥8 用例） |
| `tests/test_report_features.py` | 研报特征计算测试（≥6 用例） |
| `tests/test_signal_features.py` | 信号特征计算测试（≥6 用例） |

---

## 三、数据存储布局

```
data/raw/
├── reports/
│   └── {code}.parquet      # 列：date, code, analyst_count, report_count
│
├── eps/
│   └── {code}.parquet      # 列：date, code, eps_consensus_cur, eps_next_year, eps_revision
│
└── lhb/
    └── YYYY-MM-DD.parquet  # 列：date, code, lhb_net_buy, lhb_buy_amount, lhb_sell_amount
```

### 前视偏差控制（集中在 assembler 层）

所有新因子统一规则：`available_date = publish_date + 1 交易日`

assembler 的 merge 操作使用 `available_date ≤ kline.date`，确保当日特征不包含当日发布的数据。

---

## 四、Collector 设计

### ReportCollector

```python
class ReportCollector(BaseCollector):
    def fetch_one(self, code: str, mode: str = "report",
                  since: date | None = None) -> pd.DataFrame | None:
        """mode="report": 拉东财研报列表
           mode="eps":    拉同花顺 EPS 共识
           落地路径由 mode 决定。
        """

    def fetch_all(self, codes: list[str], mode: str = "report",
                  incremental: bool = True,
                  max_errors: int = 50) -> CollectStats:
        """批量拉取，tqdm 进度，连续失败熔断。"""

    def load(self, code: str, mode: str = "report") -> pd.DataFrame | None:
        """从本地缓存加载，不触发网络请求。"""
```

- 落地路径：`data/raw/reports/{code}.parquet`（mode="report"）、`data/raw/eps/{code}.parquet`（mode="eps"）
- 输出格式：`date(datetime64) + code(str) + 业务列`，按 date 升序，无重复行

### SignalCollector

```python
class SignalCollector(BaseCollector):
    def fetch_all(self, codes: list[str] = [],
                  date: date | None = None,
                  incremental: bool = True,
                  max_errors: int = 10) -> CollectStats:
        """全市场日报模式，一次请求拿到当天所有上榜股票。
           codes 参数忽略（全市场粒度）。
           落地到 data/raw/lhb/YYYY-MM-DD.parquet。
        """

    def fetch_one(self, code: str, since: date | None = None) -> pd.DataFrame | None:
        """为满足 BaseCollector 接口，内部调用 fetch_all 实现。"""

    def load(self, code: str) -> pd.DataFrame | None:
        """从所有日期文件聚合该股历史上榜记录。"""

    def load_market(self, date: date | None = None) -> pd.DataFrame | None:
        """读取单日全市场龙虎榜数据。"""
```

- 落地路径：`data/raw/lhb/YYYY-MM-DD.parquet`
- 同日重复拉取：drop_duplicates，保留最新

---

## 五、特征计算模块

### report.py

```python
def add_report_features(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """左连接研报因子到单股 K 线，返回新 DataFrame（不修改原始）。

    新增列：
    - analyst_count      : 覆盖机构数（当期有效值，前向填充）
    - report_count_30d   : 近30日研报数（滚动计数）
    - eps_consensus_cur  : 当年 EPS 共识预测（元）
    - eps_revision       : EPS 修订方向（+1 上调 / 0 不变 / -1 下调）

    前视偏差：merge 时用 available_date（publish_date + 1）作为连接键。
    缺失数据：全部填 NaN，由预处理层填均值。
    """
```

### signal.py

```python
def add_signal_features(
    df: pd.DataFrame,
    code: str,
    lhb_market: pd.DataFrame | None,
    north: pd.DataFrame | None,
) -> pd.DataFrame:
    """左连接信号因子到单股 K 线，返回新 DataFrame。

    新增列（龙虎榜）：
    - lhb_net_buy_30d    : 近30日龙虎榜净买入（元）
    - lhb_count_30d      : 近30日上榜次数

    迁移自 indicators.py（逻辑完全不变）：
    - north_net_5d       : 近5日累计北向净买入
    - north_net_trend    : 北向资金动量（ma5 / ma20）

    前视偏差：龙虎榜 merge 时偏移 1 天（lhb_date + 1 ≤ kline.date）。
    缺失数据：全部填 NaN。
    """
```

---

## 六、assembler.py 改动

**调用顺序（`_build_single_stock` 内部）：**

```
旧：_merge_fundamentals → _merge_fund_flow → _merge_northbound → add_all_features
新：_merge_fundamentals → _merge_fund_flow → _merge_report → _merge_signal → add_all_features
                                                                             ↑ signal 内部包含北向计算
```

**具体改动：**
1. 新增 `_merge_report(kline, code)` —— 加载研报/EPS 缓存，左连接，应用 available_date 偏移
2. 新增 `_merge_signal(kline, code, lhb_market, north)` —— 加载龙虎榜/北向，左连接，应用偏移
3. 原 `_merge_northbound` 调用改为在 `_merge_signal` 内部处理（_merge_northbound 函数可保留供内部复用）
4. `indicators.py` 中 `_add_fund_flow_features` 的北向计算部分删除，改由 `add_signal_features` 负责

---

## 七、测试用例清单

### test_report_collector.py（≥10 用例）

| 用例 | 验证内容 |
|------|---------|
| `test_fetch_one_report_saves_parquet` | 拉取研报后文件落盘，列齐全 |
| `test_fetch_one_eps_saves_parquet` | EPS 数据落盘，列齐全 |
| `test_fetch_one_returns_none_on_api_error` | API 异常返回 None，不抛出 |
| `test_fetch_one_incremental_skips_existing` | 已有缓存时 incremental 跳过 |
| `test_fetch_all_report_counts_ok_fail` | ok/fail 统计正确 |
| `test_fetch_all_eps_counts_ok_fail` | EPS 分支统计正确 |
| `test_fetch_all_circuit_breaker` | 连续 N 次失败后熔断 |
| `test_load_report_returns_dataframe` | 读研报缓存返回 DataFrame |
| `test_load_eps_returns_dataframe` | 读 EPS 缓存返回 DataFrame |
| `test_load_returns_none_when_missing` | 缓存不存在返回 None |

### test_signal_collector.py（≥8 用例）

| 用例 | 验证内容 |
|------|---------|
| `test_fetch_all_saves_daily_parquet` | 全市场日报落盘到正确路径 |
| `test_fetch_all_correct_filename` | 文件名格式 YYYY-MM-DD.parquet |
| `test_fetch_all_dedup_same_date` | 同日重复拉取不产生重复行 |
| `test_fetch_all_returns_none_on_api_error` | API 异常，stats.fail 递增 |
| `test_load_aggregates_across_dates` | load(code) 正确聚合多日文件 |
| `test_load_returns_none_when_no_data` | 无历史数据返回 None |
| `test_load_market_reads_single_date` | load_market(date) 读对应日期文件 |
| `test_load_market_returns_none_when_missing` | 日期无数据返回 None |

### test_report_features.py（≥6 用例）

| 用例 | 验证内容 |
|------|---------|
| `test_analyst_count_forward_filled` | 研报数前向填充正确 |
| `test_report_count_30d_rolling` | 近30日滚动计数正确 |
| `test_eps_revision_direction` | 上调/下调方向编码正确（+1/-1） |
| `test_no_lookahead_bias_report` | available_date 偏移有效 |
| `test_missing_report_data_fills_nan` | 无研报数据时输出 NaN |
| `test_missing_eps_data_fills_nan` | 无 EPS 数据时输出 NaN |

### test_signal_features.py（≥6 用例）

| 用例 | 验证内容 |
|------|---------|
| `test_lhb_net_buy_30d_rolling` | 近30日净买入滚动正确 |
| `test_lhb_count_30d_rolling` | 近30日上榜次数滚动正确 |
| `test_no_lookahead_bias_lhb` | 龙虎榜 +1 偏移有效 |
| `test_north_signal_migrated_values` | north_net_5d/trend 数值与迁移前 indicators.py 完全一致 |
| `test_missing_lhb_fills_nan` | 无龙虎榜数据时输出 NaN |
| `test_missing_north_fills_nan` | 无北向数据时输出 NaN |

---

## 八、执行时序

按优化计划（`docs/2026-05-14 程序优化方案及计划.md`）：

```
Day 1-2: report_collector.py + report.py（TDD）+ assembler 对接
Day 3-4: signal_collector.py + signal.py（TDD）+ indicators.py 北向迁移 + assembler 对接
Day 5:   全量重跑 Phase 1 → Phase 2 --rolling，IC 评估
Day 6-7: 增量重训 + scan 对比（新旧模型选股结果差异分析）
```

---

## 九、决策记录

1. **方案 A（独立模块 + 最小改动 assembler）** —— 与现有 northbound/fund_flow 模式完全对齐，YAGNI，测试最干净
2. **ReportCollector 合并研报和 EPS** —— 一个类两个 mode 分支，接口统一，文件数最少
3. **SignalCollector 全市场日报** —— 一次请求 vs 5644 次逐股请求，效率差距显著
4. **北向信号迁移到 signal.py** —— 统一管理所有信号类因子，indicators.py 职责更纯粹
5. **前视偏差集中在 assembler 层管控** —— 单一入口，不分散在各 feature 函数内部
