#!/bin/bash
# 自动使用虚拟环境运行量化系统
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/../quant_env/bin/activate"
cd "$SCRIPT_DIR"
python3 main.py "$@"
