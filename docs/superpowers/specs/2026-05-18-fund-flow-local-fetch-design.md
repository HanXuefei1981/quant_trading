# 设计文档：个股资金流向本地下载脚本

**日期**：2026-05-18  
**状态**：已确认  

---

## 背景与问题

个股主力资金流向数据来自东方财富接口（`push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`），通过 akshare 的 `stock_individual_fund_flow()` 封装调用。

当前问题：
- Claude Code 运行环境使用境外 IP，东方财富对境外 IP 有更严格的限频策略
- 即使 2s 间隔，在约 1800 只后触发封锁，连续 100 次失败后终止
- 当前覆盖率仅 531/5641（9.4%），资金流因子对绝大多数股票为 NaN

目标：使用**国内 IP**（本机关闭 VPN 后）一次性完成全量 5641 只股票的资金流历史下载，覆盖率达到 **80% 以上**。

---

## 约束

| 约束 | 说明 |
|------|------|
| 数据时效 | 全量历史（非仅近期） |
| 数据来源 | 仅免费接口 |
| 运行环境 | 本机终端，VPN 关闭，使用项目 `.venv` |
| Claude Code | 下载期间不参与（VPN 关闭无法使用） |
| 覆盖率目标 | ≥ 80%（约 4500 只） |

---

## 方案选择

选择**方案 C：独立脚本**（`scripts/fetch_fund_flow_local.py`）。

排除方案 A/B（`main.py` 命令）的原因：Claude Code 运行需要 VPN，与下载需要关 VPN 互斥，无法同时满足。

---

## 架构设计

### 文件位置

```
scripts/fetch_fund_flow_local.py   ← 新增，独立可执行脚本
data/fund_flow/{code}.parquet      ← 已有，缓存路径不变
data/fund_flow/_failed.txt         ← 新增，失败股票记录
```

### 模块依赖

脚本通过 `sys.path.insert(0, ROOT)` 复用项目现有模块：
- `src.data.fund_flow`：复用 `_normalize_fund_flow()`、`_market_str()`、`FUND_FLOW_DIR` 常量
- `src.data.tdx_reader`：复用 `get_all_tdx_codes()` 获取股票列表
- `tqdm`：进度条（已在 `.venv` 中）
- `akshare`：数据拉取（已在 `.venv` 中）

不引入任何新依赖。

### 执行流程

```
1. 从 data/raw/kline/ 扫描全量股票代码列表（5641 只）
2. 过滤已有 data/fund_flow/{code}.parquet → 得到待下载列表（断点续传）
3. 打印启动摘要（总量、已缓存、待下载、预计时长）
4. tqdm 进度条逐只拉取：
   a. 调用 akshare.stock_individual_fund_flow()
   b. 标准化列名（复用现有逻辑）
   c. 写入 parquet 缓存
   d. 失败：记录到 failed_codes 列表，继续
   e. 每 200 只打印阶段统计
5. 结束：打印覆盖率报告，失败列表写入 _failed.txt
```

### 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--delay` | `0.3` | 每只股票请求间隔（秒），国内 IP 可用更小值 |
| `--max-errors` | `200` | 连续失败上限，超出则暂停提示后退出 |
| `--codes` | 无（全量） | 指定股票代码，空格分隔，用于补单 |

### 运行方式

```bash
# 全量下载（在项目根目录，VPN 关闭后运行）
.venv/bin/python scripts/fetch_fund_flow_local.py

# 自定义延迟
.venv/bin/python scripts/fetch_fund_flow_local.py --delay 0.5

# 只补指定失败股票
.venv/bin/python scripts/fetch_fund_flow_local.py --codes 000001 600036 300001
```

### 输出示例

**启动摘要：**
```
股票总量：5641 只
已缓存：  531 只（跳过）
待下载：  5110 只
预计时长：~26 分钟（按 0.3s/只估算）
```

**结束报告：**
```
========================================
  资金流向下载完成
  成功：4823 只   缓存复用：531 只   失败：287 只
  覆盖率：94.5%（5354 / 5641）
  失败列表已写入 data/fund_flow/_failed.txt
========================================
下一步：开启 VPN → 在 Claude Code 中运行 python main.py 1
```

---

## 数据流与后续衔接

```
[关 VPN] 运行脚本 → data/fund_flow/{code}.parquet（5000+ 只）
[开 VPN] Claude Code:
  python main.py 1        ← Phase 1 特征工程（读取 fund_flow 缓存）
  python main.py 2        ← Phase 2 模型重训
  python main.py scan     ← 选股扫描
```

Phase 1 的 assembler 在构建截面特征时，会为每只有 `fund_flow` 缓存的股票填入 `major_net_inflow_5d` 等资金流因子，无缓存的股票该因子仍为 NaN（模型可处理）。

---

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| 单只股票网络超时/返回空 | 记录到 failed_codes，继续下一只 |
| 列名无法识别（akshare 版本差异） | 标准化逻辑已有多候选列名，兜底失败则跳过 |
| 连续失败超过 max-errors | 打印警告（"可能被限频，建议稍后重试"），写 _failed.txt 后退出 |
| 北京市场股票（8x 前缀） | `_market_str()` 返回 None，直接跳过（东方财富不支持北交所资金流） |

---

## 不在范围内

- 自动定时调度（cron）：非本次需求
- 增量每日更新：通过 `main.py update` 的北向资金逻辑处理，不在本脚本范围
- 代理 / IP 轮换：当前方案依赖用户手动切换网络环境
