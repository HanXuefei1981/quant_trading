"""全局配置"""
import os
from pathlib import Path

# 加载 .env 文件（存在时）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# Tushare Pro 配置（从 .env 读取，不要硬编码）
TUSHARE_TOKEN: str = os.getenv("TUSHARE_TOKEN", "")
TUSHARE_HTTP_URL: str = os.getenv("TUSHARE_HTTP_URL", "http://lianghua.nanyangqiankun.top")

# 通达信本地数据目录（可通过环境变量 TDX_VIPDOC_DIR 覆盖）
TDX_VIPDOC_DIR = Path(os.getenv("TDX_VIPDOC_DIR", str(Path.home() / "tdx_data")))

# 数据库路径（存放于 Elements 扩展硬盘，可通过环境变量覆盖）
DB_PATH = Path(os.getenv("QUANT_DB_PATH", "/Volumes/Elements/5、投资/quant_trading/quant.duckdb"))

# 数据参数（通达信数据从 2021-08-02 起）
START_DATE = "20210101"
END_DATE = "20991231"   # 远未来：自动使用所有已下载的 TDX 数据
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

# 截面分位标签参数（替代固定阈值，适配牛熊切换）
LABEL_TOP_PCT = 0.3       # 截面前 30% 打涨标签
LABEL_BOTTOM_PCT = 0.3    # 截面后 30% 打跌标签

# 因子预处理参数
MAD_SIGMA = 5.0           # MAD 去极值倍数（中位数 ± 5×MAD）

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
MIN_TRADE_DAYS = 250      # 至少上市 250 个交易日（约 1 年）
EXCLUDE_ST = True         # 排除 ST / *ST 股（用 akshare 实时名称列表核对）
EXCLUDE_NEW = True        # 排除新股（上市不足 MIN_TRADE_DAYS）
EXCLUDE_KCYB = True       # 排除科创板（688xxx），需开通科创板权限方可交易
ST_CACHE_DAYS = 7         # ST 列表本地缓存有效天数
