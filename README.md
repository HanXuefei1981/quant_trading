# A股量化交易系统

[![CI](https://github.com/HanXuefei1981/quant_trading/actions/workflows/ci.yml/badge.svg)](https://github.com/HanXuefei1981/quant_trading/actions/workflows/ci.yml)

基于 **LightGBM + Ridge 集成模型** 的 A 股多因子选股策略，覆盖数据同步、特征工程、模型训练、组合回测和实时扫描的完整闭环。**面向散户实战可执行**。

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
        │ python main.py fetch-fund               # 拉取基本面（PE/PB/市值等）
        ▼
   data/fundamentals/{code}.parquet
        │
        │ python main.py 1                        # 特征工程：56 个因子
        ▼
   data/processed/market_features.parquet
        │
        │ python main.py 2                        # LightGBM 三分类 + Ridge 集成
        ▼
   data/models/{lgbm,ridge,ensemble_meta}.json/.joblib
        │
        ├─→ python main.py 3                      # Top-K 组合回测
        │     data/backtest/{equity_curve,trades,report}
        │
        └─→ python main.py scan --top-k 10        # 散户实战：最新截面 Top-K
              data/backtest/scan_YYYY-MM-DD.csv
```

---

## 五大子命令

| 命令 | 用途 | 关键说明 |
|------|------|---------|
| `python main.py sync --zip <path>` | 同步通达信压缩包 | 解析文件名日期 -1 作为期望截止，与实际数据核对，需用户确认才能进入 phase1 |
| `python main.py fetch-fund [--sample N] [--delay S]` | 拉取基本面缓存 | 自动过滤退市股，每只 PE/PB/市值/换手率，缓存到 `data/fundamentals/` |
| `python main.py 1 [--sample N]` | Phase 1：数据 + 特征 | 通达信 K 线 + 基本面合并 → 56 个因子（含 10 个基本面因子）|
| `python main.py 2` | Phase 2：模型训练 | LightGBM 三分类 + Ridge 回归，按 IC 加权集成 |
| `python main.py 3 [--top-k 50 --rebalance 5]` | Phase 3：组合回测 | 含费率、板块上限、换手率上限 |
| `python main.py scan --top-k 10 [--replace-only]` | 实时扫描 | 用最新截面（无标签）出 Top-K 候选，散户模式 `replace_only` 只换被淘汰股 |

---

## 因子体系（共 56 个）

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

> ⚠️ 基本面因子代码已实施，**模型重训进行中**（fetch-fund 全量拉取 5641 只股票约 10 小时）。下面"模型评估"段为重训前的旧模型基线。

---

## 模型设计

- **算法**：LightGBM 多分类（`num_class=3`） + Ridge 回归，按验证集 IC 加权集成
- **标签**：截面分位法（前 30% 涨 / 后 30% 跌 / 中间震荡），自适应牛熊
- **预处理**：MAD 去极值 → 板块中性化 → 截面 Z-score
- **时序切分**：训练 70% / 验证 15% / 测试 15%，严格无未来信息

---

## 模型评估（基线版，未含基本面）

| 集合 | 样本量 | 准确率 | F1 | IC |
|------|--------|--------|------|------|
| 训练集 | 4,415,306 | 0.466 | 0.403 | **0.1135** |
| 验证集 | 1,031,190 | 0.462 | 0.391 | **0.0606** |
| 测试集 | 999,168 | 0.458 | 0.381 | **0.0211** |

> IC 在验证集仍处于 0.05 之上的有效阈值，但测试集衰减明显，是接下来加入基本面因子的重要动机。

---

## 散户实战模式

### scan 命令

输出当前最新截面的 Top-K 候选清单（CSV 格式），含板块、信号值、全市场分位。

`--replace-only` 模式（推荐散户）：
- 持仓股只要仍在 Top-(K × 1.5) 缓冲池内**就不动**
- 跌出缓冲池才卖出
- 用所得现金等额买入新候选
- **不做存量再平衡**，符合散户低频操作习惯

### 20 万散户交易体系

`data/散户交易手册_20万版.pdf` 包含：仓位架构、建仓/调仓规则、风控红线、费用控制、操作军规。

---

## 项目结构

```
quant_trading/
├── config/
│   └── settings.py             # 全局参数（日期范围、阈值、过滤规则）
├── src/
│   ├── data/
│   │   ├── tdx_reader.py       # 通达信 .day 解析 + 退市股过滤
│   │   ├── pipeline.py         # Phase 1 主流程 + 基本面合并
│   │   ├── fundamentals.py     # akshare 基本面拉取（PE/PB/市值）
│   │   ├── stock_filter.py     # ST 股票列表（akshare 缓存）
│   │   └── fetcher.py          # akshare K 线（备用数据源）
│   ├── features/
│   │   ├── indicators.py       # 56 个因子计算
│   │   ├── label.py            # 截面分位标签
│   │   └── preprocessing.py    # MAD + 板块中性化 + Z-score
│   ├── models/
│   │   ├── trainer.py          # LightGBM + Ridge 集成
│   │   └── evaluator.py        # IC / 方向准确率 / F1
│   ├── backtest/
│   │   ├── engine.py           # 回测引擎 + 信号生成
│   │   ├── portfolio.py        # 目标权重 + 板块上限 + 换手率限制
│   │   └── metrics.py          # 夏普 / 卡玛 / 回撤报告
│   └── utils/
│       └── logger.py
├── tests/                       # pytest 测试套件（9 个测试模块）
├── scripts/                     # 实验与稽核工具
│   ├── audit_tdx_data.py       # 通达信数据完整性稽核
│   ├── g1_no_vol_features.py   # 实验：屏蔽波动率因子
│   ├── g2_cross_sectional_label.py
│   └── gen_*.py
├── docs/
│   └── dev-log/                # 按日开发日志（README 含规范）
├── data/                        # 本地数据（部分 .gitignore）
│   ├── fundamentals/           # 基本面缓存（ignored）
│   ├── processed/              # 特征数据（ignored，约 5GB）
│   ├── models/                 # 模型权重 + 评估结果
│   └── backtest/               # 回测产物（ignored）
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
- [2026-05-13](docs/dev-log/2026-05-13.md)：引入基本面因子 + 建立开发日志制度
- [2026-05-12](docs/dev-log/2026-05-12.md)：sync 数据 + Top-10 扫描 + 散户交易手册
- [2026-05-11](docs/dev-log/2026-05-11.md)：sync 命令 + ST/科创板过滤 + scan 推断模式

---

## 待改进方向

- [ ] **全量重训**：fetch-fund 完成后跑 phase1+phase2，验证基本面因子能否显著提升 IC
- [ ] **资金流向因子**（基本面第二步）：北向资金、主力资金净流入、融资余额变化
- [ ] **事件驱动因子**：大股东增减持、回购、商誉减值预警
- [ ] **NLP 情绪因子**：股吧讨论热度、新闻情感、分析师评级变化
- [ ] **IC 逐月衰减诊断**，定位测试集 IC 衰减原因
- [ ] **Walk-Forward 滚动窗口**，验证策略时间稳定性
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
- akshare（基本面 + ST 列表）
- fpdf2（散户交易手册 PDF）
- pytest（测试）

```bash
pip install -r requirements.txt
```

---

## 风险提示

本项目为个人量化研究工具，所有信号、回测、推荐均不构成投资建议。A 股政策风险显著，**实盘前请在小资金验证**。
