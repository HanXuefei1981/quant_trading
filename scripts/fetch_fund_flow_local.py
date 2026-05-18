"""
个股资金流向本地下载脚本（关 VPN 后在终端运行）

使用方式：
  .venv/bin/python scripts/fetch_fund_flow_local.py           # 全量
  .venv/bin/python scripts/fetch_fund_flow_local.py --delay 0.5
  .venv/bin/python scripts/fetch_fund_flow_local.py --codes 000001 600036
"""
import argparse
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tqdm import tqdm

from src.data.fund_flow import FUND_FLOW_DIR, fetch_fund_flow

KLINE_DIR = ROOT / "data" / "raw" / "kline"


def get_all_codes(kline_dir: Path = KLINE_DIR) -> list[str]:
    """返回 kline 目录下所有股票代码（parquet stem），按字母排序。"""
    return sorted(p.stem for p in kline_dir.glob("*.parquet"))


def get_pending_codes(all_codes: list[str], fund_flow_dir: Path = FUND_FLOW_DIR) -> list[str]:
    """过滤掉已有本地缓存的股票，返回待下载列表。"""
    return [c for c in all_codes if not (fund_flow_dir / f"{c}.parquet").exists()]
