# UI 监控台设计文档

**日期**：2026-05-26  
**项目**：quant_trading A股量化交易系统  
**状态**：已审批，待实现

---

## 一、目标

构建一个**单页 Web 监控台**，替代纯命令行操作，实现：

- 在浏览器中触发 ingest / Phase1 / Phase2 / Phase3 / scan 等流程
- 实时查看数据水位、接口覆盖状态、特色因子覆盖率
- 查看最新模型指标和回测权益曲线
- 查看最新选股信号表
- 通过 SSE 流式接收命令执行日志

---

## 二、技术方案

| 层 | 选型 | 原因 |
|----|------|------|
| 后端 | FastAPI + Python | 项目已是 Python 生态，零额外依赖 |
| 前端 | 原生 HTML / CSS / JS | 无构建步骤，单文件 `monitor.html` |
| 实时日志 | SSE（Server-Sent Events） | 比 WebSocket 更简单，单向流适合日志场景 |
| 数据读取 | DuckDB + Parquet 文件直读 | 复用项目现有 DAL |
| 启动 | `python monitor.py` | 单命令，无需额外配置 |

运行方式：

```bash
python monitor.py          # 默认 http://localhost:8765
python monitor.py --port 9000
```

---

## 三、文件结构

```
quant_trading/
├── monitor.py             # FastAPI 应用主入口
├── monitor/
│   ├── api/
│   │   ├── status.py      # GET /api/status — 数据水位、模型指标汇总
│   │   ├── run.py         # POST /api/run/{cmd} — 触发命令执行
│   │   └── stream.py      # GET /api/stream/{task_id} — SSE 日志流
│   ├── readers/
│   │   ├── watermark.py   # 读取各数据源水位（kline/features/northbound 等）
│   │   ├── metrics.py     # 读取 eval_results.json、ensemble_meta.json
│   │   ├── backtest.py    # 读取回测结果（Phase 3 输出）
│   │   └── scan.py        # 读取最新 scan_*.csv
│   └── runner.py          # 子进程管理、SSE 广播
└── monitor_ui/
    └── index.html         # 单页前端（内联 CSS + JS）
```

---

## 四、页面布局

仪表盘网格，从上到下共 5 个区块，底部固定日志面板。

### 4.1 顶部标题栏（sticky）

- 左：标题 `⚡ 量化交易监控台`，副标题显示因子数/股票池
- 右：当前任务状态指示点（idle / running）+ 上次 scan 日期

### 4.2 数据水位（6 格，2行×3列）

每格展示一个数据集的最新状态：

| 数据集 | 水位字段 | 状态判断 |
|--------|---------|---------|
| kline | 最新交易日 | 与今日差 ≤1 交易日 → ok |
| features | 最新特征日 | 与 kline 差 > 0 → warn |
| northbound | 最新日期 | 同 kline 判断 |
| fundamentals | 覆盖股票数 | ≥90% → ok，<70% → err |
| fund_flow | 覆盖股票数 | ≥50% → ok，<20% → err |
| models | 最后训练日期 | 存在 → ok |

颜色规则：左侧竖条 green/yellow/red 对应 ok/warn/err（系统健康状态）。

### 4.3 数据接口状态表（全宽，8 行）

展示各采集接口的状态，行：

| 接口 | 对应因子 | 采集命令 | 最新数据 | 覆盖率 | 状态 | 备注 |
|------|---------|---------|---------|-------|------|------|
| TDX .day 文件 | OHLCV / 复权 | ingest / collect | 日期 | 100% | ✓ | |
| akshare 财务数据 | PE/PB/市值 | collect | 只数 | 91.8% | ✓ | |
| akshare 北向资金 | north_net | collect | 日期 | 66.7% | ✓ | |
| 东方财富 资金流向 | major_net | fetch-flow | 只数 | 10.1% | △ | 需关 VPN |
| 东方财富 龙虎榜 | lhb_net_buy | collect | — | ~45% | △ | 历史回填中 |
| 腾讯基本面 | EPS/净利/ROE | fetch-fund | — | 0% | ✗ | 接口待调试 |
| 研报评级 | report_rating | — | — | 0% | ✗ | 数据源待选 |
| mootdx TCP 增量 | K线增量 | update | 实时 | 100% | ✓ | |

### 4.4 特色因子表（全宽，9 行）

展示区别于普通技术指标的特色因子，含覆盖率进度条：

| 因子名 | 分类 | 接口 | 覆盖率 | 说明 | 状态 |
|-------|------|------|-------|------|------|
| north_net_5d | 北向资金 | akshare 港股通 | 66.7% | 5日北向净买入累计 | ✓ |
| north_net_trend | 北向资金 | akshare 港股通 | 66.7% | 20日线性回归斜率 | ✓ |
| lhb_net_buy | 龙虎榜 | 东方财富 | ~45% | 机构/游资净买入额 | △ |
| major_net_buy_1d | 主力资金 | 东方财富 | 10.1% | 当日主力净流入 | ✗ |
| major_net_buy_5d | 主力资金 | 东方财富 | 10.1% | 5日主力净流入 | ✗ |
| pe_ttm | 基本面 | akshare | 91.8% | 市盈率 TTM | ✓ |
| pb / market_cap | 基本面 | akshare | 91.8% | 市净率 / 流通市值 | ✓ |
| eps_yoy | 研报/EPS | 腾讯（待建） | 0% | EPS 同比增长率 | ✗ |
| report_rating | 研报/EPS | 待选型 | 0% | 分析师评级/目标价 | ✗ |

### 4.5 中部三列（2fr : 1.1fr : 1.5fr）

#### 流程控制台（左，最宽）

按 4 类分组，每组有分隔标题：

**数据采集**
- `update`（日常增量一键，primary 样式，置顶）
- `ingest --zip`
- `collect`
- `fetch-fund`（warn 样式，注明需关 VPN）
- `fetch-flow`（warn 样式，注明需关 VPN）

**特征工程**
- `Phase 1`（特征计算）

**模型训练**
- `Phase 2 --rolling`
- `Phase 2 --final`

**组合回测**
- `Phase 3`
- `scan --top-k 50`

按钮状态：idle / running（绿色脉冲）/ done / warn。  
点击后：在底部日志面板实时输出 stdout，按钮切换为 running 状态，完成后变 done。

#### 模型指标（中）

- 训练集 IC / 验证集 IC / 测试集 IC（读取 eval_results.json）
- 验证集准确率 / F1
- 因子分组标签（技术×46、基本面×10、北向×2、资金流×4 等）
- 最后训练日期

**颜色规则（A股涨跌色）**：IC 为正 → 红，IC 为负 → 绿；准确率、F1 用灰色（中性信息）。

#### 回测权益曲线（右）

- SVG 折线图（策略线红色、基准线灰色）
- 4 个统计卡：超额收益（红）、最大回撤（绿）、夏普比率（蓝）、回测区间（灰）
- 图例

### 4.6 选股信号表（全宽）

读取最新 `data/backtest/scan_YYYY-MM-DD.csv`，展示全部持仓行（Top-50）+ 缓冲区前5行（共约55行，超出可滚动）：

列：排名 | 代码 | 板块 | 收盘价 | 信号值（+进度条）| 全市场分位 | 北向5日净买 | 主力净流入 | 状态（持仓/缓冲区）

颜色：净流入红色（涨色）、净流出绿色（跌色）、信号值进度条红色。

### 4.7 底部日志面板（fixed，高度 200px）

- 标题行：当前运行命令名 + 绿色脉冲点
- 右侧：收起/展开切换
- 日志区：SSE 流式追加，带时间戳，按级别着色（info/warn/success/progress）

---

## 五、A股颜色规范

| 场景 | 颜色 | 色值 |
|------|------|------|
| 涨 / 正收益 / 净流入 / 正值 | 红 | `#f87171` |
| 跌 / 负收益 / 净流出 / 负值 | 绿 | `#4ade80` |
| 系统状态 OK | 绿边框/徽章 | `#22c55e` |
| 系统状态 WARN | 黄边框/徽章 | `#f59e0b` |
| 系统状态 ERR | 红边框/徽章 | `#ef4444` |
| 信息类数值（夏普、比率） | 蓝 | `#60a5fa` |
| 弱化信息 | 灰 | `#94a3b8` |

> 注意区分：金融涨跌色（红=涨、绿=跌）与系统健康状态色（绿=正常、红=异常）共存，语义不同，不冲突。

---

## 六、API 设计

### GET /api/status

返回页面所需全部数据，一次性加载：

```json
{
  "watermarks": {
    "kline":        { "date": "2026-05-15", "count": 5641, "status": "ok" },
    "features":     { "date": "2026-05-08", "rows": 6480000, "cols": 62, "status": "warn" },
    "northbound":   { "date": "2026-05-18", "coverage": 0.667, "status": "ok" },
    "fundamentals": { "count": 5182, "coverage": 0.918, "status": "ok" },
    "fund_flow":    { "count": 572, "coverage": 0.101, "status": "err" },
    "models":       { "date": "2026-05-19", "w_lgbm": 0.54, "w_ridge": 0.46, "status": "ok" }
  },
  "metrics": {
    "train_ic": -0.0129, "val_ic": -0.0209, "test_ic": -0.0230,
    "val_accuracy": 0.385, "val_f1": 0.350,
    "last_train": "2026-05-19"
  },
  "backtest": {
    "strategy_return": 0.6294, "benchmark_return": 0.4785,
    "excess_return": 0.1509, "max_drawdown": -0.183,
    "sharpe": 1.42,
    "period": ["2024-01-01", "2026-05-18"],
    "equity_curve": {
      "dates": ["2024-01-02", "2024-01-03", "..."],
      "strategy": [1.0, 1.008, "..."],
      "benchmark": [1.0, 1.003, "..."]
    }
  },
  "scan": {
    "date": "2026-05-18",
    "top_n": 50, "buffer_n": 75,
    "signals": [
      { "rank": 1, "code": "300511", "sector": "创业板", "close": 6.76,
        "signal": 1.725, "pct": 0.998, "north_5d": 2341, "fund_flow": 1820000,
        "status": "hold" },
      ...
    ]
  }
}
```

### POST /api/run/{cmd}

触发命令执行，cmd 枚举值：
`ingest`, `collect`, `fetch-fund`, `fetch-flow`, `update`, `phase1`, `phase2-rolling`, `phase2-final`, `phase3`, `scan`

返回：
```json
{ "task_id": "abc123", "cmd": "phase1", "started_at": "2026-05-26T09:41:00" }
```

### GET /api/stream/{task_id}

SSE 流，每行格式：

```
data: {"ts": "09:41:03", "level": "info", "msg": "Phase 1 开始..."}\n\n
data: {"ts": "09:42:11", "level": "progress", "msg": "3901/5641 69%"}\n\n
data: {"ts": "09:45:00", "level": "success", "msg": "Phase 1 完成"}\n\n
data: {"type": "done", "exit_code": 0}\n\n
```

---

## 七、数据读取说明

所有读取通过现有 `config/settings.py` 获取路径，不硬编码：

| reader | 读取来源 |
|--------|---------|
| watermark.py | DuckDB `kline` 表 max(date)；`data/features/market_features.parquet`；`data/northbound/`；`data/fundamentals/`；`data/fund_flow/`；`data/models/` |
| metrics.py | `data/models/eval_results.json`，`data/models/ensemble_meta.json` |
| backtest.py | `data/backtest/backtest_result.csv`（或 Phase3 输出路径） |
| scan.py | `data/backtest/scan_YYYY-MM-DD.csv`（取最新一个） |

---

## 八、运行和安全约束

- 监控台仅供本机使用（`127.0.0.1`），不对外暴露
- 命令执行通过白名单校验，不接受任意 shell 字符串
- 同一时间只允许一个命令运行（第二个请求返回 409）
- 子进程 stdout/stderr 实时转发到 SSE，进程结束发送 `done` 事件

---

## 九、不在本期范围内

- 用户认证 / 权限控制
- 历史日志持久化（本期只看当次运行）
- 多任务并发执行
- 移动端适配
- 参数输入 UI（如 `--top-k` 的数值调整）
