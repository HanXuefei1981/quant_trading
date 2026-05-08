"""全局配置"""
import os
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# 通达信本地数据目录（Windows 侧，通过 WSL /mnt 访问）
TDX_VIPDOC_DIR = Path("/mnt/e/new_tdx/vipdoc")

# 数据参数（通达信数据从 2021-08-02 起）
START_DATE = "20210101"
END_DATE = "20260507"
ADJUST = "qfq"  # 前复权（通达信日线已含复权价，无需额外处理）

# 技术指标参数
MA_WINDOWS = [5, 10, 20, 60]
EMA_WINDOWS = [12, 26]
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
BOLL_WINDOW = 20
BOLL_STD = 2
ATR_PERIOD = 14
KDJ_PERIOD = 9

# 特征预测目标
FORWARD_DAYS = 5          # 预测未来 N 日收益
UP_THRESHOLD = 0.03       # 涨幅 > 3% 为看涨
DOWN_THRESHOLD = -0.03    # 跌幅 < -3% 为看跌

# 回测参数
COMMISSION_RATE = 0.0003  # 双向手续费 万3
STAMP_DUTY = 0.001        # 印花税 千1（卖方）
SLIPPAGE = 0.002          # 滑点 0.2%
INITIAL_CAPITAL = 1_000_000  # 初始资金 100万

# 模型参数
TRAIN_RATIO = 0.7
VAL_RATIO = 0.15
TEST_RATIO = 0.15
RANDOM_SEED = 42

# 股票过滤规则
MIN_TRADE_DAYS = 250      # 至少上市 250 个交易日
EXCLUDE_ST = True         # 排除 ST 股
EXCLUDE_NEW = True        # 排除新股（上市不足 MIN_TRADE_DAYS）
