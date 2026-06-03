# A股量化交易系统

[![CI](https://github.com/HanXuefei1981/quant_trading/actions/workflows/ci.yml/badge.svg)](https://github.com/HanXuefei1981/quant_trading/actions/workflows/ci.yml)

基于 **LightGBM + Ridge 集成模型** 的 A 股多因子选股策略，覆盖数据同步、特征工程、模型训练、组合回测和实时扫描的完整闭环。**面向散户实战可执行**。

📖 **[程序执行手册](docs/执行手册.md)** — 首次建库、日常增量更新、选股扫描的完整操作步骤与全参数说明
📊 **[特征表数据覆盖说明书](docs/特征表数据覆盖说明书.md)** — features 表每列的来源、覆盖范围与 NULL 成因，建模/回测前查字段可用性

---

## 设计理念

A 股是散户主导的情绪市场，相对于美股需要更注重：

- **截面分位标签**（替代固定阈值 ±3%），自适应牛熊切换
- **板块中性化** + 截面 Z-score，消除板块系统性暴露
- **技术因子 + 基本面因子** 双轨，捕捉情绪与价值偏离
- **散户级别风控**：交易权限过滤（ST / 科创板）、上市天数过滤、退市股快速剔除

---

## 工作流概览

```
通达信全量数据.zip
        │
        │ python main.py sync --zip <path>       # 解压 + 日期核对 + 用户确认
        ▼
   .sync_state.json（门控状态）
        │
        ├─→ python main.py collect [--refresh]   # TDX .day → parquet（首次/强制全量）
        │
        │ python main.py fetch-fund               # 拉取基本面（PE/PB/市值等）
        ▼
   data/fundamentals/{code}.parquet
        │
        │ python main.py 1                        # 特征工程：62 个因子
        ▼
   data/processed/market_features.parquet
        │
        │ python main.py 2 [--rolling]            # LightGBM 三分类 + Ridge 集成
        ▼
   data/models/{lgbm,ridge,ensemble_meta}.json/.joblib
        │
        ├─→ python main.py 3                      # Top-K 组合回测
        │     data/backtest/{equity_curve,trades,report}
        │
        └─→ python main.py fetch-basic            # 拉取股票名称/行业（scan 富信息表用，一次即可）
              │
              └─→ python main.py scan --top-k 10   # 散户实战：最新截面 Top-K（含名称/行业/估值/财务）
                    data/backtest/scan_YYYY-MM-DD.csv

   # 日常增量更新（无需新 zip，关 VPN + 有效 tushare token）
   python main.py update          # tushare 批量拉当日 K 线 + 北向
   python main.py fetch-fund      # 基本面增量（亦可 --since 回填缺口）
   python main.py fetch-flow      # 资金流向 + 北向增量
   python main.py 1               # 增量重建特征到最新交易日
   python main.py scan            # 最新推荐
```

---

## 子命令一览

| 命令 | 用途 | 关键说明 |
|------|------|---------|
| `python main.py sync --zip <path>` | 同步通达信压缩包 | 解析文件名日期 -1 作为期望截止，与实际数据核对，需用户确认才能进入 phase1 |
| `python main.py collect [--refresh]` | TDX .day → parquet | 默认跳过已有文件；`--refresh` 强制全量重转换（更新已有股票数据必须用此参数）|
| `python main.py fetch-fund [--since YYYY-MM-DD] [--delay S]` | 按交易日批量拉取基本面 | tushare `daily_basic`，单次取全市场当日快照（快约 5000×）→ DuckDB `fundamentals`；`--since` 从次日起回填缺口（幂等 INSERT OR REPLACE）|
| `python main.py fetch-flow [--delay S]` | 拉取资金流向 + 北向 | tushare `moneyflow` 批量 → `fund_flow`，含北向 `moneyflow_hsgt`（T+1）|
| `python main.py fetch-financial` | 拉取财务指标 | ROE/ROA/毛利率/净利率/营收/净利润等 → `financial_indicator`（季频）|
| `python main.py fetch-reports` | 拉取研报 / EPS 共识 | → `reports` / `eps_snapshot` |
| `python main.py fetch-basic` | 拉取股票名称 / 行业 | tushare `stock_basic` → DuckDB `stock_basic`，供 scan 关联名称与行业；名称/行业变动少，需要时手动重跑刷新 |
| `python main.py 1 [--sample N]` | Phase 1：数据 + 特征 | K 线 + 基本面 + 信号合并 → 因子；逐日流式预处理写表（低内存）|
| `python main.py 2 [--rolling] [--final]` | Phase 2：模型训练 | LightGBM 三分类 + Ridge 回归，按 IC 加权集成；`--rolling` 滚动窗口，`--final` 全量重训生产模型 |
| `python main.py 3 [--top-k 50 --rebalance 5]` | Phase 3：组合回测 | 含费率、板块上限、换手率上限 |
| `python main.py scan --top-k 10 [--confirm K] [--holdings c1,c2]` | 实时扫描 | 最新截面 Top-K 富信息表（名称/行业/估值/财务 + **连榜**）；`--confirm K` 仅把连续 K 次在榜的标为✓可建仓（过滤一日游）；`--holdings` 输出 继续持有/卖出/新建仓 三栏。存 `data/backtest/scan_YYYY-MM-DD.csv`（需先 `fetch-basic`）|
| `python main.py update` | 日常增量更新 | tushare 批量拉当日全市场 K 线 + 北向（T+1）；随后跑 `1` 增量重建特征 |

---

## 因子体系（共 62 个）

### 技术因子（46 个）

| 类别 | 数量 | 代表因子 |
|------|------|---------|
| 趋势 | 8 | `ma5/10/20/60_ratio`, `macd_dif/dea/hist/cross/positive/slope` |
| 震荡 | 7 | `rsi`, `rsi_oversold/overbought`, `kdj_k/d/j`, `kdj_cross` |
| 波动 | 9 | `boll_width/pct/breakout`, `atr`, `atr_ratio`, `volatility5/20`, `high_low_ratio`, `amplitude_ma5` |
| 量价 | 12 | `vol_ratio/trend`, `turnover_ratio/trend`, `ret1/3/5/10/20/60/120`, `open_close_ratio`, `upper/lower_shadow`, `log_price` |
| 情绪 / 形态 | 6 | `momentum_quality`, `reversal`, `price_vol_agree`, `up_streak`, `bull_score`, `macd_hist_slope` |
| EMA | 4 | `ema12/26`, `macd_dif` 派生 |

### 基本面因子（10 个）

| 因子 | 含义 |
|------|------|
| `pe_ttm_log` / `pb_log` / `ps_log` / `pcf_log` | 估值 log（截面 Z-score 后体现"贵不贵"） |
| `market_cap_log` / `float_mc_log` | 市值规模（小盘股情绪弹性） |
| `pe_ttm_self_pct` / `pb_self_pct` | 自身 252 日历史分位 |
| `float_shares_chg20` | 流通股本 20 日变化（增发/回购痕迹） |
| `peg_value` | PEG 值 |

### 量价信号因子（6 个，M3）

| 因子 | 含义 | 覆盖率 |
|------|------|--------|
| `north_net_5d` | 北向资金 5 日净买入 | 66.7% |
| `north_net_trend` | 北向资金趋势方向 | 66.7% |
| `lhb_net_buy_30d` | 龙虎榜 30 日净买入额 | 部分 |
| `lhb_count_30d` | 龙虎榜 30 日上榜次数 | 部分 |
| `eps_consensus_cur` | EPS 一致预期（待积累） | ~0%* |
| `report_count_30d` | 研报数量 30 日（待验证） | 部分* |

> \* `eps_consensus_cur` / `report_count_30d` / `analyst_count` 当前已排除训练，待月度快照积累后重新验证。

---

## 模型设计

- **算法**：LightGBM 多分类（`num_class=3`） + Ridge 回归，按验证集 IC 加权集成
- **标签**：截面分位法（前 30% 涨 / 后 30% 跌 / 中间震荡），自适应牛熊
- **预处理**：MAD 去极值 → 板块中性化 → 截面 Z-score
- **时序切分**：训练 70% / 验证 15% / 测试 15%，严格无未来信息

---

## 模型评估（2026-05-19 重训，含基本面 + 北向因子）

| 集合 | 样本量 | 准确率 | F1 | IC |
|------|--------|--------|------|------|
| 训练集 | ~4.5M | — | — | **0.1240** |
| 验证集 | ~1M | — | — | **0.0807** |
| 测试集 | ~1M | — | — | **0.0197** |

> 验证集 IC 较上一版（0.0723）提升 11.6%，测试集 IC 较上一版（0.0150）提升 31%。

### Phase 3 回测表现（截至 2026-05-25 清洗后数据）

| 指标 | 策略 | 基准（等权全市场）|
|------|------|------|
| 总收益率 | **62.94%** | 47.85% |
| 超额收益 | +15.09% | — |

> 回测数据经过数据清洗：剔除 40 只借壳重组股（借壳导致单日 50,000%+ 异常涨幅），还原真实市场基准。

---

## 散户实战模式

### scan 命令

输出当前最新截面的 Top-K 候选清单，控制台为全中文富信息表、同时存 CSV。字段：排名 / 代码 / 名称 / 行业 / 收盘价 / 信号值 / 信号分位 / 总市值(亿) / 市盈率 / 市净率 / 市销率 / 净资收益率 / 净利润同比。前 K 只为建仓候选、K+1~K×1.5 为观察缓冲区。

> 名称/行业来自 `stock_basic` 表，首次使用前需先运行 `python main.py fetch-basic`（之后偶尔刷新即可）；估值取当日 `fundamentals`、财务取最新 `financial_indicator`。

`--replace-only` 模式（推荐散户）：
- 持仓股只要仍在 Top-(K × 1.5) 缓冲池内**就不动**
- 跌出缓冲池才卖出
- 用所得现金等额买入新候选
- **不做存量再平衡**，符合散户低频操作习惯

**防一日游 / 减少反复调仓（`--confirm` + `--holdings`）**：

`连榜` 列记录每只票"截至今日连续在 Top-buffer 的次数"——单日异动冲榜的票连榜=1。

- `--confirm K`：只把**连榜≥K** 且在 Top-k 的票标为 `✓可建仓`，连榜不足标 `⏳观察`（疑似一日游，建议观望）。
- `--holdings c1,c2,...`：传入现持仓，按 replace-only 输出三栏 **继续持有**（仍在 Top-buffer）/ **卖出**（已跌出）/ **新建仓**（Top-k 且连榜达标且未持有）。

> 实战纪律建议：每 5 个交易日操作一次 + `--confirm 2~3` + `--holdings`，配合行业上限，可有效避开"今天暴涨冲榜、明天反转套牢"的一日游股票。

### 20 万散户交易体系

`data/散户交易手册_20万版.pdf` 包含：仓位架构、建仓/调仓规则、风控红线、费用控制、操作军规。

---

## 项目结构

```
quant_trading/
├── config/
│   └── settings.py             # 全局参数（日期范围、阈值、过滤规则、EXCLUDE_KCYB、DB_PATH）
├── src/
│   ├── collectors/             # 数据采集器（tushare 私有代理 → DuckDB）
│   │   ├── base.py             # BaseCollector + CollectStats
│   │   ├── tdx_collector.py    # 全市场批量 K 线（tushare daily，update 用）
│   │   ├── fundamental_collector.py  # 基本面 daily_basic（collect_batch 按日批量）
│   │   ├── fund_flow_collector.py    # 资金流向 moneyflow
│   │   ├── northbound_collector.py   # 北向资金 moneyflow_hsgt（T+1）
│   │   ├── signal_collector.py       # 龙虎榜 top_list（collect 当日 / backfill 回填）
│   │   ├── report_collector.py       # 研报 report_rc + EPS 共识
│   │   ├── financial_collector.py    # 财务指标 fina_indicator
│   │   └── tencent_collector.py      # 腾讯财经 PE/PB 快照（可选）
│   ├── dal/                    # 数据访问层（DuckDB 单写多读）
│   │   ├── connection.py       # 连接单例 get_db（支持 read_only）
│   │   ├── schema.py           # 建表 migrate（kline/fundamentals/features/stock_basic …）
│   │   ├── raw_repo.py         # 原始表读写（含 nullable→float64 防御）
│   │   ├── feature_repo.py     # features 表读写（ON CONFLICT upsert）
│   │   └── meta_repo.py        # 水位（collect_log）读写
│   ├── data/
│   │   ├── tushare_client.py   # tushare Pro 初始化 + 私有代理 URL（关 VPN 运行）
│   │   ├── tushare_fetchers.py # 各类 tushare 拉取函数（K线/基本面/资金流/北向/龙虎榜/研报/财务/stock_basic）
│   │   ├── ingest_zip.py       # 通达信 hsjday.zip → DuckDB kline 直接解析
│   │   ├── tdx_reader.py       # 通达信 .day 解析 + 退市股过滤
│   │   ├── watermark.py        # 水位管理（data/watermark.json）
│   │   ├── pipeline.py         # Phase 2/3 特征加载（从 features 表，过滤无标签行）
│   │   ├── stock_filter.py     # ST / *ST 列表（akshare 缓存，scan/回测过滤用）
│   │   └── fundamentals.py · fetcher.py · fund_flow.py  # 旧 akshare 数据源（已被 tushare 取代，保留备用）
│   ├── features/
│   │   ├── indicators.py       # 技术因子计算
│   │   ├── label.py            # 截面分位标签（top/bottom 30%）
│   │   ├── preprocessing.py    # MAD 去极值 + 板块中性化 + Z-score
│   │   ├── signal.py           # 量价信号因子（北向/龙虎榜/研报）
│   │   ├── report.py           # 研报 / EPS 因子
│   │   ├── assembler.py        # 多源合并 + 逐日流式预处理写表（assemble / incremental / inference）
│   │   └── scan_enrich.py      # scan 富信息拼装（名称/行业 + 估值 + 财务）
│   ├── models/
│   │   ├── trainer.py          # LightGBM + Ridge 集成（rolling / walk-forward / final）
│   │   └── evaluator.py        # IC / 方向准确率 / F1
│   ├── backtest/
│   │   ├── engine.py           # 回测引擎 + 信号生成 + 等权基准
│   │   ├── portfolio.py        # 目标权重 + 板块上限 + 换手率限制
│   │   └── metrics.py          # 夏普 / 卡玛 / 回撤报告
│   └── utils/
│       └── logger.py
├── tests/                       # pytest 测试套件
├── scripts/                     # 稽核 / 实验脚本（audit_tdx_data、gen_* 等）
├── docs/
│   ├── 执行手册.md              # 全流程操作手册
│   ├── 特征表数据覆盖说明书.md   # features 表字段来源/覆盖/NULL 成因
│   └── dev-log/                # 按日开发日志
├── data/
│   ├── quant.duckdb             # 中央数据库（DB_PATH，常置于外置盘；raw + features 全在此）
│   ├── watermark.json           # 各源采集水位
│   ├── models/                  # 模型权重 + 评估结果
│   └── backtest/                # 回测产物 + scan_YYYY-MM-DD.csv
└── main.py                      # 统一入口
```

---

## 数据要求

- **通达信本地数据**：通过环境变量 `TDX_VIPDOC_DIR` 指定，默认 `~/tdx_data`
- 目录结构：`{sh,sz,bj}/lday/{prefix}{code}.day` 二进制日线
- 起始日期：`config.settings.START_DATE = "20210101"`
- **公开仓库不含原始数据**：约 5GB+，需本地通达信导出

---

## 关键配置（`config/settings.py`）

```python
START_DATE = "20210101"              # 数据起点
END_DATE = "20991231"                # 数据终点（远未来，永远使用全部已下载数据）

LABEL_TOP_PCT = 0.3                  # 截面前 30% 标涨
LABEL_BOTTOM_PCT = 0.3               # 截面后 30% 标跌

MIN_TRADE_DAYS = 250                 # 排除上市不足 250 日
EXCLUDE_ST = True                    # 排除 ST / *ST（akshare 实时核对）
EXCLUDE_KCYB = True                  # 排除科创板（688xxx）
ST_CACHE_DAYS = 7                    # ST 列表缓存有效期

COMMISSION_RATE = 0.0003             # 万 3 手续费
STAMP_DUTY = 0.001                   # 千 1 印花税（卖方）
SLIPPAGE = 0.002                     # 0.2% 滑点
INITIAL_CAPITAL = 1_000_000          # 100 万初始资金
```

---

## 开发日志

按日记录每次变更：动机、影响范围、验证方式、待跟进。详见 [`docs/dev-log/README.md`](docs/dev-log/README.md)。

最近变更：
- [2026-06-01](docs/dev-log/2026-06-01.md)：Phase1 逐日流式预处理（省 ~8GB 内存）+ 修复 9 个陈旧测试 + 基本面缺口补全（fetch-fund --since）+ 滚动重训回测（夏普 2.57）+ scan 富信息表（fetch-basic）+ 同步至 05-29 + 特征表数据覆盖说明书
- [2026-05-20](docs/dev-log/2026-05-20-m3-ic-validation.md)：M3 因子 IC 验收；北向 bug 修复；EPS/研报/LHB 因子效果分析，暂排除出训练
- [2026-05-19](docs/dev-log/2026-05-19.md)：修复 sync macOS 解压 Windows zip 路径 bug；全量数据更新（TDX 至 2026-05-18）；测试集 IC 提升 31%
- [2026-05-18](docs/dev-log/2026-05-18.md)：资金流向本地下载脚本（TDD 全流程，14 个测试）
- [2026-05-13](docs/dev-log/2026-05-13.md)：引入基本面因子 + 建立开发日志制度
- [2026-05-12](docs/dev-log/2026-05-12.md)：sync 数据 + Top-10 扫描 + 散户交易手册
- [2026-05-11](docs/dev-log/2026-05-11.md)：sync 命令 + ST/科创板过滤 + scan 推断模式

---

## 待改进方向

- [ ] **全量重训含基本面**：fetch-fund 全量拉取（5641 只，约 10 小时）完成后跑 phase1+phase2，验证基本面因子能否显著提升 IC
- [ ] **EPS 定时采集**：每月初运行 `collect_m3_data.py eps`，积累 ≥2 年月度快照后重新加入训练
- [ ] **资金流向覆盖率**：当前 10.1%（570/5641），关 VPN 后用 `scripts/fetch_fund_flow_local.py` 批量下载到 80%+
- [ ] **LHB / 研报因子有效性分析**：截面 IC 时序分析，找到真正有效的子期间后重新启用
- [ ] **watermark 修正**：`data/watermark.json` 中 `kline` 字段需更新为实际数据截止日期
- [ ] **Walk-Forward 滚动验证**：已有 `--rolling` 参数，需系统性测试时间稳定性
- [ ] **IC 逐月衰减诊断**：定位测试集 IC 衰减原因，评估是否有 regime 切换效应
- [ ] **Regime 识别**：趋势 / 震荡 / 危机分状态建模
- [ ] **基准切换**：当前等权全市场，考虑改沪深 300

---

## 环境依赖

- Python 3.10+
- lightgbm ≥ 4.6
- pandas / numpy
- scikit-learn
- joblib
- matplotlib
- akshare（基本面 + ST 列表 + 北向资金）
- fpdf2（散户交易手册 PDF）
- pytest（测试）

```bash
pip install -r requirements.txt
```

---

## 风险提示

本项目为个人量化研究工具，所有信号、回测、推荐均不构成投资建议。A 股政策风险显著，**实盘前请在小资金验证**。
