"""统一日志配置"""
import logging
import sys
from pathlib import Path


def setup_logger(name: str = "quant", level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger()
    if root.handlers:
        return logging.getLogger(name)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    log_dir = Path(__file__).parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    fh = logging.FileHandler(log_dir / "quant.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    root.setLevel(level)

    # 屏蔽第三方库的 DEBUG 日志
    for noisy in ("lightgbm", "matplotlib", "PIL", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger(name)
