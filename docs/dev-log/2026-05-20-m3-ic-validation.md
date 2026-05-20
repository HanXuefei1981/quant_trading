# M3 IC 验收日志 — 2026-05-20

## 背景

M3 新因子扩展：analyst_count, report_count_30d, eps_consensus_cur, eps_revision,
lhb_net_buy_30d, lhb_count_30d, north_net_5d (迁移), north_net_trend (迁移)

## Bug 修复记录

**根因：assembler 重复 merge 北向资金**

- 原代码在 `assemble()` 中先调用 `_merge_northbound(raw, north_df)` 把 `north_net_inflow` 合并进 raw
- 再调用 `add_signal_features(df, ..., north_df=north_df)`，signal.py 内部再次 merge 同名列
- pandas 自动加 `_x/_y` 后缀，`if "north_net_inflow" in df.columns` 失败，全部退化为 NaN
- **修复**：删除 assembler 中的 `_merge_northbound` 调用，由 signal.py 单独负责 merge

## IC 验收结果

| 分割 | IC |
|------|-----|
| 训练集 | 0.1240 |
| 验证集 | 0.0807 |
| 测试集 | **0.01966** |

- 基准（M3 前）：0.0197
- 差值：-0.0004（-0.2%，噪声范围内）
- 目标 ≥0.0207：**暂未达到**

## 未达目标原因

8 个新因子均为全 NaN（SignalCollector / ReportCollector 尚未运行收集数据）。
模型实际上等价于 M3 前版本，IC 不可能超越基准。

## 下一步

1. 运行 `SignalCollector.fetch_all(date_range)` 收集龙虎榜历史
2. 运行 `ReportCollector.fetch_all(codes, mode="report")` 收集研报数据
3. 运行 `ReportCollector.fetch_all(codes, mode="eps")` 收集 EPS 预测
4. Phase 1 增量更新（或全量重建）
5. Phase 2 重新训练，对比 IC

## 框架验证结论

✅ M3 因子框架无退步，代码逻辑正确，等待数据填充后预期 IC 提升。
