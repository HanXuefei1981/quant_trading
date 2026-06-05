# 设计文档：修复 monitor scan 读取器（中文列 schema）+ 显示连榜

- 日期：2026-06-05
- 状态：已批准
- 背景：启动 monitor 服务后 `/api/status` 返回 500。根因 `monitor/readers/scan.py:get_scan()` 按**旧英文列**（`code/rank/segment/close/signal/signal_pct`）读 scan CSV，但 scan CSV 早已改为**中文富信息列**（`排名,代码,名称,行业,板块,收盘价,信号值,信号分位,总市值亿,市盈率,市净率,市销率,净资收益率,净利润同比,连榜`，由 `scan_enrich.py` 生成）→ `KeyError: 'code'`。其单测 `tests/monitor/test_scan.py` 用旧英文列假数据，故一直绿、未抓到漂移。

## 1. 目标

`get_scan()` 读当前中文列 scan CSV，`/api/status` 恢复正常；顺带把「连榜」呈现到监控台信号表。

## 2. 非目标（YAGNI）

- 不为旧英文格式做兼容回退（reader 取最新文件，最新恒为新格式）。
- 不改 `monitor/api/status.py`（`dataclasses.asdict` 自动序列化新增字段）。
- 不改其他 reader（watermark/metrics/backtest）。

## 3. 现状（事实基线）

- 新 CSV 表头（带 BOM）：`排名,代码,名称,行业,板块,收盘价,信号值,信号分位,总市值亿,市盈率,市净率,市销率,净资收益率,净利润同比,连榜`。原始值示例：`1,301135,瑞德智能,元器件,创业板,17.68,1.6361...,1.0,...,2`。`信号分位` 已是 0-1 分数；`代码` 存为整数（需 zfill 6）；`板块`=创业板（→segment）。
- `SignalRow`（scan.py）字段：`rank, code, segment, close, signal, signal_pct, north_5d, fund_flow, status`。
- `get_scan()` 现：`pd.read_csv(dtype={"code": str})` → `df["code"]/df["rank"]/df["segment"]/df["close"]/df["signal"]/df["signal_pct"]`（全英文，崩）。
- `status.py`：`"scan": dataclasses.asdict(get_scan(data_dir))` → 新增字段自动进 JSON。
- `index.html:renderScan`：信号表行 8 列（rank/code/segment/close/signal/signal_pct/fund_flow/status），空态 `colspan="8"`，消费 `s.rank/s.code/s.segment/s.close/s.signal/s.signal_pct/s.fund_flow/s.status`（不消费 north_5d）。

## 4. 设计

### 4.1 `monitor/readers/scan.py`
- `SignalRow` 新增字段 `streak: Optional[int]`（连榜=截至当日连续在榜次数）。
- `get_scan()` 重写列访问，用中文列名映射：

  | SignalRow | CSV 中文列 | 处理 |
  |-----------|-----------|------|
  | rank | 排名 | int |
  | code | 代码 | `str(int(float(c))).zfill(6)` |
  | segment | 板块 | str（缺则 "—"）|
  | close | 收盘价 | float |
  | signal | 信号值 | float |
  | signal_pct | 信号分位 | float（已 0-1）|
  | streak | 连榜 | int（缺则 None）|
  | north_5d | — | None（不变）|
  | fund_flow | — | `_latest_fund_flow(code)`（不变）|
  | status | — | rank<=top_n? "hold":"buffer" |

  按 `排名` 升序，取 `top_n + buffer_n` 行。
- **优雅降级**：读 CSV 后，若必需列（至少 `排名`、`代码`、`信号值`、`信号分位`）缺失，记 warning 并返回空 `ScanData(date=date_str, ...)`，不抛异常。

### 4.2 `monitor_ui/index.html`
- 信号表加「连榜」列：在表头对应 `<thead>` 加 `<th>连榜</th>`（置于 status 列前）；`renderScan` 行模板在 status `<td>` 前插 `<td>${s.streak!=null?s.streak:'—'}</td>`；空态 `colspan="8"` 改 `colspan="9"`。

### 4.3 `tests/monitor/test_scan.py`
- `_make_scan_csv`/`_sample_rows` 改为产出**新中文列**（含 `连榜`）。
- 既有断言改读 SignalRow 字段（rank/code/segment/close/signal/signal_pct 不变）。
- 新增：`SignalRow.streak` 取值正确；CSV 缺必需列 → 返回空 ScanData（不抛）。

## 5. 错误处理
- 缺必需列：返回空 ScanData（防 500）。
- `板块`/`连榜` 个别缺：segment→"—"，streak→None（行级容错）。

## 6. 测试
见 4.3。覆盖：正常中文 CSV→signals 含 streak；rank 排序与 hold/buffer 划分；缺列优雅降级。

## 7. 影响面 / 风险
- 改动集中在 scan reader + 其测试 + index.html 一列；status.py 无需改。
- 优雅降级使 monitor 对未来 CSV 格式变化更鲁棒（不再整页 500）。
