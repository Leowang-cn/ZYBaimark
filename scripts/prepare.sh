#!/usr/bin/env bash
# 部署环境准备：Python 依赖、无头浏览器（可选）、前端构建。
set -euo pipefail
cd "$(dirname "$0")/.."

# 部署平台默认 Node 可能过旧，优先使用平台提供的 Node 24/22/20
for d in /usr/local/node24/bin /usr/local/node22/bin /usr/local/node20/bin; do
  if [ -x "$d/node" ]; then export PATH="$d:$PATH"; break; fi
done
PY="${PYTHON:-python3}"

echo "== 版本检查"
"$PY" -c 'import sys; assert sys.version_info >= (3, 9), "需要 Python 3.9+"; print("python", sys.version.split()[0])'
node -e 'const v=+process.versions.node.split(".")[0]; if (v<20) {console.error("需要 Node 20+，当前 "+process.version); process.exit(1)} console.log("node", process.version)'
ldd --version 2>/dev/null | head -1 || true
for b in "${FFMPEG_BIN:-ffmpeg}" "${FFPROBE_BIN:-ffprobe}"; do
  if command -v "$b" >/dev/null 2>&1; then echo "$b: $("$b" -version 2>/dev/null | head -1)"; else echo "警告：未找到 $b，视频处理将失败"; fi
done

echo "== Python 依赖"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade "pip>=23" -q
.venv/bin/python -m pip install --prefer-binary -r requirements.txt

echo "== 无头浏览器（扫码登录用，失败不影响其他功能，可改用粘贴 Cookie）"
if .venv/bin/python -m playwright install chromium-headless-shell; then
  echo "浏览器已安装"
else
  echo "警告：无头浏览器安装失败，扫码登录不可用"
fi

echo "== 前端构建"
npm ci
npm run build
echo "== 环境准备完成"
