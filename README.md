# A股量化交易系统

[![CI](https://github.com/HanXuefei1981/quant_trading/actions/workflows/ci.yml/badge.svg)](https://github.com/HanXuefei1981/quant_trading/actions/workflows/ci.yml)

基于 LightGBM 的 A 股多因子选股策略，涵盖数据管道、特征工程、模型训练与组合回测完整流程。

## 项目概览

```
数据源（通达信本地）→ 30个技术因子 → LightGBM三分类 → Top-K组合回测
```

| 阶段 | 模块 | 说明 |
|------|------|------|
| Phase 1 | 数据管道 + 特征工程 | 通达信 .day 文件 → OHLCV → 30个技术因子 |
| Phase 2 | 模型训练 | LightGBM 三分类（涨/震荡/跌），严格时序切分 |
| Phase 3 | 回测引擎 | Top-K 等权组合，含手续费/印花税/滑点 |

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# Phase 1：数据提取 + 特征工程
python main.py 1

# Phase 2：模型训练
python main.py 2

# Phase 3：回测
python main.py 3
```

## 项目结构

```
quant_trading/
├── config/
│   └── settings.py          # 全局参数配置
├── src/
│   ├── data/
│   │   ├── pipeline.py      # Phase 1 数据提取主流程
│   │   └── tdx_reader.py    # 通达信 .day 文件解析
│   ├── features/
│   │   └── indicators.py    # 30个技术因子计算
│   ├── models/
│   │   ├── trainer.py       # LightGBM 训练 + 时序切分
│   │   └── evaluator.py     # IC / 方向准确率 / F1 评估
│   └── backtest/
│       ├── engine.py        # 回测引擎（Top-K + 调仓）
│       └── metrics.py       # 绩效指标（夏普/卡玛/回撤）
├── scripts/
│   ├── g1_no_vol_features.py        # 实验：屏蔽波动率因子
│   ├── g2_cross_sectional_label.py  # 实验：截面排名标签
│   └── audit_tdx_data.py            # 通达信数据质量稽核
├── data/
│   ├── models/              # 训练好的模型及评估结果
│   └── backtest/            # 回测净值曲线与交易记录
└── main.py                  # 统一入口
```

## 因子体系

共 **30 个技术因子**，分四类：

| 类别 | 因子数 | 代表因子 |
|------|--------|----------|
| 趋势类 | 8 | ma5/10/20/60_ratio、macd_dif/dea/hist/cross |
| 震荡类 | 6 | rsi、rsi_oversold/overbought、kdj_k/d/j |
| 波动类 | 7 | boll_width、boll_pct、atr_ratio、volatility5/20、high_low_ratio、atr |
| 量价类 | 9 | vol_ratio、vol_trend、ret1/5/10/20、open_close_ratio、upper/lower_shadow |

## 模型设计

- **算法**：LightGBM 多分类（`num_class=3`）
- **标签**：未来 5 日收益率三分类（>+3% 涨，<-3% 跌，其余震荡）
- **时序切分**：训练 70% / 验证 15% / 测试 15%，无未来信息泄漏

| 集合 | 日期范围 | 样本量 |
|------|----------|--------|
| 训练集 | 2021-01-05 ~ 2024-09-18 | 4,409,984 |
| 验证集 | 2024-09-19 ~ 2025-07-09 | 1,031,065 |
| 测试集 | 2025-07-10 ~ 2026-04-27 | 999,031 |

## 模型评估结果

| 模型 | 验证集 IC | 测试集 IC | 早停轮数 |
|------|-----------|-----------|----------|
| 原模型 | 0.0902 | 0.0503 | — |
| G1（屏蔽波动率因子） | 0.0893 | 0.0540 | 74 |
| G2（截面排名标签） | 0.0573 | 0.0249 | 36 |

> IC（信息系数）= Pearson(P涨 - P跌, future_ret)，> 0.05 视为有效因子

## 回测结果

回测区间：2024-09-19 ~ 2026-04-27（386 个交易日）

| 指标 | 策略（Top-20，20日调仓） | 基准（等权全市场） |
|------|--------------------------|-------------------|
| 总收益率 | 19.84% | 107.34% |
| 年化收益率 | 12.57% | 61.17% |
| 夏普比率 | 0.127 | 2.136 |
| 最大回撤 | -51.25% | -17.05% |
| 卡玛比率 | 0.245 | 3.588 |

## 回测参数

| 参数 | 值 |
|------|----|
| 初始资金 | 100 万元 |
| 手续费 | 万3（双向） |
| 印花税 | 千1（卖方） |
| 滑点 | 0.2% |
| 持仓数量 | Top-20 |
| 调仓频率 | 每 20 个交易日 |

## 数据说明

- **数据源**：通达信本地 `.day` 文件（`/mnt/e/new_tdx/vipdoc/`）
- **股票池**：全 A 股 5,767 只，过滤 ST / 上市不足 250 日后剩余 5,523 只
- **数据规模**：6,440,080 行 × 51 列
- **注意**：原始 parquet 数据文件（约 2GB）不含在仓库中，需本地通达信重新生成

## 待改进方向

- [ ] IC 逐月衰减诊断，定位模型失效时间点
- [ ] 动态阈值标签（自适应波动率替代固定 ±3%）
- [ ] Walk-Forward 滚动窗口验证
- [ ] 引入 Regime 识别（趋势/震荡/危机分状态建模）
- [ ] 融合基本面因子（PE/PB/ROE）
- [ ] 基准改用沪深300或中证500
- [ ] 波动率倒数加权仓位管理

## 环境依赖

- Python 3.10+
- lightgbm
- pandas / numpy
- scikit-learn
- joblib
- matplotlib
- reportlab（用于生成 PDF 报告）
