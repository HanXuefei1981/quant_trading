# 设计文档：daily 无新数据时 fail-fast

- 日期：2026-06-05
- 状态：已批准
- 背景：2026-06-05 运营 `main.py daily` 跑 06-04 日更时，tushare token 失效（"无效的 token"），update/fetch-fund/fetch-flow 三步均 `新拉取=0`。但 daily 未中止——采集器把 token 失败记为 warning + skipped（非异常），daily 只对异常 fail-fast，于是继续空转 Phase1 增量 ~16 分钟、再 scan 旧截面。需让 daily 在无新交易日数据时提前中止。

## 1. 目标

`daily` 在 `update` 未获取到新交易日数据（`kline_stats.ok == 0`）时，记录清晰错误并以退出码 2 提前中止，跳过 fetch-fund/fetch-flow/phase1/scan，避免无意义空转。

## 2. 非目标（YAGNI）

- 不改采集器把 token 失败从 `skipped` 改记为 `fail`（更大改动；本次仅在 daily 层用 `ok==0` 判定）。
- 不改 runner/UI（`daily` 命令名与映射不变）。
- 不让独立 `main.py update` 在无新数据时抛异常（非交易日运行 update 是合法的，不应报错）。
- 不对 fetch-fund/fetch-flow 单独设闸（phase1/scan 只依赖 kline；gate 在 update 的 kline 结果即可）。

## 3. 现状（事实基线）

- `CollectStats`（`src/collectors/base.py`）字段：`ok`(新拉取) / `fail` / `cached` / `skipped`。
- `update(args)`（`main.py`）：`kline_stats = tdx.collect_tushare_daily_batch(...)`；`if kline_stats.ok > 0: 更新水位`；函数**返回 None**。token 失效时 `ok=0`、失败计入 `skipped`（`fail=0`）。
- `daily(args)`（`main.py`）：`copy.copy(args)` 后置 `full=False`，按 `[(name, fn), ...]` 顺序 `fn(inner_args)`，仅对异常 fail-fast（`except Exception: raise SystemExit(1)`）。
- 现有测试 `tests/test_main_commands.py`：`test_daily_runs_steps_in_order`、`test_daily_fail_fast` 的 `update` mock 返回 None。

## 4. 设计

### 4.1 `update()` 返回 kline 统计
在 `update()` 末尾 `return kline_stats`。向后兼容：独立命令与 runner 忽略返回值，行为不变。

### 4.2 `daily()` 增加无新数据闸口
把 update 步从通用循环拆出，先跑并捕获返回：
```
inner_args = copy.copy(args); inner_args.full = False
# update 步
try:
    stats = update(inner_args)
except Exception:
    logger.exception("一键日更在步骤 [update] 失败，已中止"); raise SystemExit(1)
if stats is None or stats.ok == 0:
    logger.error("一键日更：update 未获取到新交易日数据（非交易日 / 数据未就绪 / token 失效？），已中止，后续步骤跳过")
    raise SystemExit(2)
# 其余步：保持现有 fail-fast 循环
for name, fn in [("fetch-fund", fetch_fund), ("fetch-flow", fetch_flow), ("1(增量)", phase1), ("scan", scan)]:
    logger.info(f"===== 一键日更 ▶ {name} =====")
    try: fn(inner_args)
    except Exception: logger.exception(f"一键日更在步骤 [{name}] 失败，已中止"); raise SystemExit(1)
logger.info("===== 一键日更完成 =====")
```
保留 update 步开头的 `logger.info("===== 一键日更 ▶ update =====")`。

### 4.3 退出码语义
- `0`：全链成功。
- `1`：某步抛异常（崩溃）。
- `2`：update 无新交易日数据，提前中止。

## 5. 错误处理 / 边界
- `stats is None`（理论上不会，但防御）按无新数据处理 → 退出 2。
- 非交易日 / 数据未就绪 / token 失效三种情况都表现为 `ok==0`，统一中止并在日志提示三种可能，由用户判断。

## 6. 测试
- `update()` 返回 `CollectStats`（kline 统计）——通过源码/行为断言。
- daily：update 返回 `ok==0` → 抛 `SystemExit(2)`，且 fetch-fund/fetch-flow/phase1/scan **均不执行**。
- daily：update 返回 `ok>0` → 正常跑完五步（更新现有 `test_daily_runs_steps_in_order`：update mock 返回 `CollectStats(ok=1)`）。
- daily：update 返回 `ok>0` 后某步抛异常 → `SystemExit(1)`（更新现有 `test_daily_fail_fast`：update mock 返回 `CollectStats(ok=1)`）。

## 7. 文档
- `docs/程序运行说明手册.md` daily 段补一句：无新交易日时提前中止（退出码 2）。

## 8. 影响面 / 风险
- `update()` 加返回值：低风险，向后兼容。
- 现有两个 daily 测试的 update mock 必须改为返回 `CollectStats(ok=1)`，否则会因新闸口提前中止而失败——已在测试任务覆盖。
- runner/UI 无改动。
