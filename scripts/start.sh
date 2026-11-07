#!/usr/bin/env bash
# 启动服务。
# 端口优先级：命令行（位置参数 8916 / --port 8916 / --port=8916）> 环境变量 PORT > 8000。
# 兼容部署平台自动追加的 "--port <端口> --host <地址>" 参数。
set -euo pipefail
cd "$(dirname "$0")/.."

PORT_ARG=""
HOST="0.0.0.0"
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT_ARG="${2:-}"; shift 2 || shift ;;
    --port=*) PORT_ARG="${1#--port=}"; shift ;;
    --host) HOST="${2:-0.0.0.0}"; shift 2 || shift ;;
    --host=*) HOST="${1#--host=}"; shift ;;
    *) [ -z "$PORT_ARG" ] && PORT_ARG="$1"; shift ;;
  esac
done
PORT="${PORT_ARG:-${PORT:-8000}}"
case "$PORT" in ''|*[!0-9]*) echo "端口无效：$PORT" >&2; exit 1;; esac

if [ ! -x .venv/bin/python ]; then
  echo "未找到 .venv，请先执行环境准备：bash scripts/prepare.sh" >&2
  exit 1
fi
echo "启动服务，版本 $(git rev-parse --short HEAD 2>/dev/null || echo unknown)，监听 $HOST:$PORT"
exec .venv/bin/python -m uvicorn backend.main:app --host "$HOST" --port "$PORT"
