# 设计文档：Phase 1 增量日更 + 全量分离 + UI 重排

- 日期：2026-06-04
- 状态：已批准（待写实现计划）
- 背景触发：2026-06-04 运营 06-03 日更时，`main.py 1` 实际调用全量 `assemble()`（重算 2021→今全表），对 exFAT USB 外置盘做数 GB 写盘，导致进程卡死在不可中断 I/O（`STAT=U`）约 8 小时后崩溃。改走增量 `assemble_incremental()`（只写新交易日）几分钟完成且无挂起。

## 1. 目标

1. 日常运行默认走**增量**特征工程，消除每日全量重算与大写盘（既慢又会拖挂 USB 盘）。
2. 全量重建保留为**显式、单独触发**的能力（应急/月初/换数据源时用）。
3. UI（monitor 面板）反映"日常增量 vs 全量重建"的区分，并提供一键日更。
4. 修复一个已存在的正确性缺陷：全量 `assemble()` 跑完不回写 features 水位，导致后续增量算错区间（当前水位停在 2026-05-26 即此因）。

## 2. 非目标（YAGNI）

- 不迁移数据库（按用户决定，主库仍在 `/Volumes/Elements` exFAT 盘）。
- 不改 Phase 2（训练）/ Phase 3（回测）逻辑——它们是批量拟合 / 一次性模拟，消费 features 表产出模型/报告，结构上无增量语义。
- 不改 scan 的 top-k 默认（UI 仍 50）。
- 不引入新的训练在线学习能力。

## 3. 现状（事实基线）

- `main.py` 用 argparse，`phase` 为带 choices 的位置参数；`phases` 字典分发到 `update / phase1 / phase2 / phase3 / scan / fetch-* / ...`。
- `main.py:phase1(args)` 当前**无条件**调用 `assemble(sample_size=args.sample)`（全量）。
- `src/features/assembler.py`：
  - `assemble(...)`：全量重建，逐日流式 upsert 写表；**不回写 meta_repo 水位**。
  - `assemble_incremental(...)`：读 `meta_repo.get_last_date("features","__market__")` 作为 `since`，对每只股票读近 300 天回看窗口算特征、只保留 `date > since` 的新行，逐日截面预处理后 upsert；**结束时回写水位**（最后有标签日）。若 `since` 为 None → 回退全量。
  - `assemble_inference(...)`：scan 专用，读最新截面，无需标签。
- UI：`monitor.py`（FastAPI）服务 `monitor_ui/index.html`（单页）；`monitor/api/run.py` 暴露 `POST /api/run/{cmd}` 与 `/api/kill`；`monitor/runner.py` 的 `CMD_MAP` 把 UI 命令名映射到 `main.py` 子进程调用，`TaskManager` 单例**单任务互斥**（`is_busy`），输出经 SSE/队列实时流式返回。

## 4. 设计

### 4.1 CLI 层（`main.py`）

| 命令 | 行为 | 改动 |
|------|------|------|
| `main.py 1` | 增量日更 → `assemble_incremental()` | `phase1()` 默认分支改为增量 |
| `main.py 1 --full` | 全量重建 → `assemble()` + 回写水位 | 新增全局 `--full` 标志；`phase1()` 增加分支 |
| `main.py daily` | 一键日更链：`update → fetch-fund → fetch-flow → 1(增量) → scan`，fail-fast | 新增 `daily` 子命令并加入 `phase` choices 与分发字典 |

实现要点：
- argparse 新增 `parser.add_argument("--full", action="store_true", dest="full", help="phase1: 全量重建(默认增量)")`。
- `phase1(args)`：`if getattr(args,"full",False): 走 assemble()，完成后将 features 水位回写为 max(有标签日)（与 assemble_incremental 末尾逻辑一致）; else: assemble_incremental()`。
- **水位回写收口**：抽一个小函数（如 `_write_features_watermark(df, meta_repo)`）供"全量分支"与 `assemble_incremental` 共用，避免重复实现、保证语义一致。
- `daily(args)`：按序调用现有 `update / fetch_fund / fetch_flow / phase1 / scan` 函数；**fail-fast**——任一步抛异常即中止链路、记录失败步骤并以非零退出码结束（便于 UI/CLI 感知）。phase1 在链中走增量（`args.full=False`）；scan 沿用 argparse 默认（`--top-k 50`、`--confirm 2`）。

### 4.2 UI 层

**`monitor/runner.py` 的 `CMD_MAP` 改动：**
```python
"phase1":       [_PYTHON, "main.py", "1"],            # 语义变为增量(main.py 1 现=增量)
"phase1-full":  [_PYTHON, "main.py", "1", "--full"],  # 新增：全量
"daily":        [_PYTHON, "main.py", "daily"],        # 新增：一键日更
```
runner 的 `_run` / `TaskManager` 逻辑**不改**（仍单任务互斥）；`daily` 作为单个子进程跑完整链，实时流式输出沿用现有机制。

**`monitor_ui/index.html` 三分区重排：**
```
【日常更新】  [✨ 一键日更]
   [update] [fetch-fund] [fetch-flow] [Phase1 增量] [scan]
【全量重建与训练】
   [Phase1 全量] [phase2-rolling] [phase2-final] [phase3]
【结果】  [最新 Top 榜] [回测]
```
按钮 `data-cmd` 对应 CMD_MAP 键（`daily / update / fetch-fund / fetch-flow / phase1 / scan / phase1-full / phase2-rolling / phase2-final / phase3`）。沿用现有调用/流式渲染逻辑，仅调整分组、文案与新增按钮。

### 4.3 文档层

- **新增** `docs/程序运行说明手册.md`：完整运行手册。涵盖 CLI 全部命令、典型场景（日常增量、一键日更、应急全量重建、月初换数据源）、UI 面板使用、故障排查（含 exFAT 盘挂起的识别与处理）。
- **更新** `README.md` 工作流段落：`1`=增量、`1 --full`=全量、新增 `daily`；子命令表同步。
- **更新** `docs/执行手册.md`：相应命令说明。

### 4.4 测试

- `phase1` 分支选择：`--full` 走 `assemble()`、缺省走 `assemble_incremental()`（用 mock/monkeypatch 断言调用了哪个）。
- 全量分支**回写水位**：assemble 完成后 meta_repo features 水位被更新为 max(有标签日)。
- `daily` 链路：顺序与 fail-fast（某步抛异常时后续不执行、退出码非零）。
- `CMD_MAP` 新键存在性与映射正确（`phase1-full`、`daily`）。

## 5. 数据流

```
日常：daily ─┬─ update      (tushare 当日K线+北向, 水位增量)
            ├─ fetch-fund  (基本面当日快照)
            ├─ fetch-flow  (资金流向+北向)
            ├─ 1 (增量)     assemble_incremental → features 表只写新交易日 + 回写水位
            └─ scan        assemble_inference 读最新截面 → 模型推断 → Top-N

应急：1 --full  assemble 全量重建 features 表 + 回写水位（与增量一致）
训练/回测：2 / 3  读整张 features 表 → 模型 / 净值曲线（不在本次范围）
```

## 6. 错误处理

- `daily` fail-fast：任一步异常即停，日志标明失败步骤，非零退出码。
- `phase1` 增量在 `since` 为 None 时（首次/水位丢失）由 `assemble_incremental` 既有逻辑回退全量——保持现有安全行为，不额外改动。
- exFAT 盘写盘挂起属外部存储问题，不在代码层绕过（遵循项目 CLAUDE.md 数据质量原则）；手册中给出识别（`STAT=U`、盘无响应）与处理（重插 USB / 重启）指引。

## 7. 影响面 / 风险

- 行为变更：`main.py 1` 从全量变增量。README/手册必须同步，避免老用户误解。已有调用 `main.py 1` 的脚本（如 cron/run.sh 包装）将自动获得增量语义——符合预期。
- 全量回写水位是**修复**而非回归：修正后全量→增量切换不再算错区间。
- UI 改动集中在 `CMD_MAP` 与 `index.html`，runner 核心不动，风险低。
