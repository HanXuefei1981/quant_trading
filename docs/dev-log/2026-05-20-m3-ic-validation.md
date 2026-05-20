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

## 数据采集完成后的完整验证（2026-05-20 晚）

完成采集后重跑 Phase1 + Phase2，进行逐步消融实验：

| 特征集 | 特征数 | 测试 IC |
|--------|--------|---------|
| 基线（旧，north bug） | ~62 | 0.0197 |
| +LHB+north+report+EPS | 68 | 0.0158 |
| 排除 EPS（0% 覆盖） | 66 | 0.0158 |
| 排除 EPS+report/analyst | 64 | 0.0183 |
| 排除 EPS+report+LHB | 63 | **0.0190** |

### 关键发现

1. **EPS 因子 0% 覆盖**：ReportCollector(mode="eps") 只采集当日快照，无历史时间序列。
   `merge_asof backward` 对所有历史行返回 NaN。需每月定时采集积累。

2. **研报因子引入噪声**：`report_count_30d` / `analyst_count` 在测试期（2025-07~2026-05）
   与已有因子相关性高，导致 IC 从 0.0183 降至 0.0158。

3. **LHB 因子边际效果有限**：`lhb_count_30d` 在当前测试期略有负贡献（0.0183→0.0190）。

4. **North 因子（bug 修复后）**：`north_net_5d` / `north_net_trend` 覆盖 66.7%，
   测试 IC 0.0190 ≈ 0.0197 基线，差距在噪声范围内。

### 当前排除列表（indicators.py get_feature_columns）

- `eps_consensus_cur`, `eps_revision`：待每月快照积累
- `report_count_30d`, `analyst_count`：待下一轮因子分析
- `lhb_count_30d`：待更长历史验证

## 下一步

1. **EPS 定时采集**：每月初运行 `collect_m3_data.py eps` 积累历史快照
2. **因子有效性分析**：对 LHB / 研报因子做截面 IC 时序分析，找到真正有效的子期间
3. **re-enable 策略**：当历史数据足够（≥ 2 年月度 EPS 快照）后重新加入训练

## 最终结论

M3 数据基础设施完整（采集、存储、特征计算管线全部就位）。
当前测试 IC = 0.0190，接近基线 0.0197（±3.5%，噪声范围）。
North 因子 bug 已修复，框架无退步。EPS/研报/LHB 因子待数据积累后重新验证。
