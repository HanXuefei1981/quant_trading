"""生成合成市场数据，用于在无网络时验证 Phase 2 流水线"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from config.settings import PROCESSED_DIR, START_DATE, END_DATE

np.random.seed(42)

N_STOCKS = 100
dates = pd.bdate_range(start=START_DATE, end=END_DATE)  # 仅工作日

rows = []
for code_idx in range(N_STOCKS):
    code = f"{100 + code_idx:06d}"
    price = 10.0
    for date in dates:
        ret = np.random.randn() * 0.015
        price = price * (1 + ret)
        rows.append({"date": date, "code": code, "close": price, "ret1": ret})

df = pd.DataFrame(rows)

# 生成特征列（与 indicators.py 输出格式一致）
def add_features(g):
    g = g.sort_values("date").copy()
    close = g["close"]
    for w in [5, 10, 20, 60]:
        ma = close.rolling(w).mean()
        g[f"ma{w}_ratio"] = close / ma - 1
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    g["macd_dif"] = dif
    g["macd_dea"] = dea
    g["macd_hist"] = (dif - dea) * 2
    g["macd_cross"] = np.sign(dif - dea).diff().fillna(0)
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    g["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    g["rsi_oversold"] = (g["rsi"] < 30).astype(int)
    g["rsi_overbought"] = (g["rsi"] > 70).astype(int)
    g["ret5"] = close.pct_change(5)
    g["ret10"] = close.pct_change(10)
    g["ret20"] = close.pct_change(20)
    g["volatility5"] = g["ret1"].rolling(5).std()
    g["volatility20"] = g["ret1"].rolling(20).std()
    g["boll_width"] = close.rolling(20).std() * 2 / (close.rolling(20).mean() + 1e-9)
    g["boll_pct"] = np.random.uniform(0, 1, len(g))
    g["atr_ratio"] = np.abs(g["ret1"])
    g["vol_ratio"] = np.random.uniform(0.5, 2, len(g))
    g["vol_trend"] = np.random.uniform(0.8, 1.2, len(g))
    g["kdj_k"] = np.random.uniform(20, 80, len(g))
    g["kdj_d"] = np.random.uniform(20, 80, len(g))
    g["kdj_j"] = 3 * g["kdj_k"] - 2 * g["kdj_d"]
    g["high_low_ratio"] = np.abs(g["ret1"]) * 1.5
    g["open_close_ratio"] = g["ret1"]
    g["upper_shadow"] = np.random.uniform(0, 0.01, len(g))
    g["lower_shadow"] = np.random.uniform(0, 0.01, len(g))
    # 标签
    future_ret = close.shift(-5) / close - 1
    g["future_ret"] = future_ret
    g["label"] = np.select(
        [future_ret >= 0.03, future_ret <= -0.03], [1, -1], default=0
    )
    return g

print("生成合成特征数据...")
parts = []
for code_idx in range(N_STOCKS):
    code = f"{100 + code_idx:06d}"
    g = df[df["code"] == code].copy()
    g = add_features(g)
    parts.append(g)
result = pd.concat(parts, ignore_index=True)
result = result.dropna(subset=["label", "ret1"]).reset_index(drop=True)

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
out = PROCESSED_DIR / "market_features.parquet"
result.to_parquet(out, index=False)
print(f"已保存至 {out}")
print(f"数据规模: {len(result)} 行, {result['code'].nunique()} 只股票")
print(f"标签分布: 跌={( result['label']==-1).sum()}, 震荡={(result['label']==0).sum()}, 涨={(result['label']==1).sum()}")
