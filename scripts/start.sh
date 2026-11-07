#!/usr/bin/env bash
# 启动服务。端口优先级：第一个参数 > 环境变量 PORT > 8000。
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-${PORT:-8000}}"
case "$PORT" in ''|*[!0-9]*) echo "端口无效：$PORT" >&2; exit 1;; esac
echo "启动服务，监听 0.0.0.0:$PORT"
exec .venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT"
