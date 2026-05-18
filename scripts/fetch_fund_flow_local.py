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


def download_all(
    codes: list[str],
    delay: float,
    max_errors: int,
) -> dict:
    """
    批量下载资金流向，返回统计字典。

    Returns:
        {"ok": int, "fail": int, "failed_codes": list[str]}
    """
    ok = 0
    fail = 0
    failed_codes: list[str] = []
    consecutive_errors = 0

    for i, code in enumerate(tqdm(codes, desc="资金流向下载", unit="只")):
        df = fetch_fund_flow(code, use_cache=False)
        if df is not None and not df.empty:
            ok += 1
            consecutive_errors = 0
        else:
            fail += 1
            failed_codes.append(code)
            consecutive_errors += 1
            if consecutive_errors >= max_errors:
                print(
                    f"\n⚠ 连续 {max_errors} 次失败，可能被限频。"
                    f"建议稍后重试剩余股票。"
                )
                break

        if (i + 1) % 200 == 0:
            print(f"\n  进度 {i+1}/{len(codes)}  成功 {ok}  失败 {fail}")

        if delay > 0 and i < len(codes) - 1:
            time.sleep(delay)

    return {"ok": ok, "fail": fail, "failed_codes": failed_codes}


def print_header(total: int, cached_count: int, pending_count: int, delay: float) -> None:
    """打印下载开始前的摘要信息。"""
    est_minutes = pending_count * delay / 60
    print(f"\n{'='*50}")
    print(f"  股票总量：{total} 只")
    print(f"  已缓存：  {cached_count} 只（跳过）")
    print(f"  待下载：  {pending_count} 只")
    print(f"  预计时长：~{est_minutes:.0f} 分钟（按 {delay}s/只估算）")
    print(f"{'='*50}\n")


def print_report(
    ok: int,
    fail: int,
    cached_count: int,
    total: int,
    failed_codes: list[str],
    fund_flow_dir: Path = FUND_FLOW_DIR,
) -> None:
    """打印下载结束报告，并将失败列表写入 _failed.txt。"""
    covered = ok + cached_count
    pct = covered / total * 100 if total > 0 else 0.0

    print(f"\n{'='*50}")
    print(f"  资金流向下载完成")
    print(f"  成功：{ok} 只   缓存复用：{cached_count} 只   失败：{fail} 只")
    print(f"  覆盖率：{pct:.1f}%（{covered} / {total}）")

    if failed_codes:
        fund_flow_dir.mkdir(parents=True, exist_ok=True)
        failed_path = fund_flow_dir / "_failed.txt"
        failed_path.write_text("\n".join(failed_codes))
        print(f"  失败列表已写入 {failed_path}")

    print(f"{'='*50}")
    print(f"\n下一步：开启 VPN → 在 Claude Code 中运行 python main.py 1\n")
