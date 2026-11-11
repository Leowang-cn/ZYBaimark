# 小数 AI 课自动打点

输入“NL 内容生产系统”的视频素材 ID，自动抽取音频、ASR 转写，按抽题规则调用大模型生成“时间点 / 问题 / 答案”打点，经人工在审阅工作台确认后导出 Excel / CSV。

设计说明见 [docs/开发方案.md](docs/开发方案.md)，部署规范见 [docs/部署要求.md](docs/部署要求.md)。

## 功能

- **新建任务**：① 粘贴视频素材 ID（自动去重、校验格式）② 登录内容生产系统：粘贴 Cookie 后即时校验并显示账号名，获取步骤默认收起；服务器支持无头浏览器时可选钉钉扫码 ③ 抽题规则默认折叠为一行摘要，点击打开侧边抽屉，可按章节（角色与目标、完整扫描、判断是否收录……）分别查看/修改，也可切换全文编辑；修改只对本任务生效。
- **处理流水线**：ffprobe 校验 → 抽音频 → 静音检测 → ASR（分段 + 词级时间戳）→ 大模型 3 轮（候选、回扫、复核）→ 时间点计算与校验。各阶段产物缓存，失败可重试，服务重启后继续排队。
- **生成版本**：每次生成产生不可变的新版本；可修改规则后对单个视频或整个任务重新生成（复用音频与转写，只重跑 AI）。版本可切换查看、与上一版对比、设为当前版本。当前版本已有人工修改时，新版本不会自动替换。
- **审阅工作台**：视频直接播放 CDN 原片（失败回退 240p HLS）；点击打点默认从老师开始提问的那句话播放（按问题文本在逐字稿中自动定位，可切换为“再往前一句”或“固定提前 N 秒”，匹配不到时退回固定提前）；每个打点按程序校验结果标绿/黄/红置信度，分析文字点击展开；进度条显示全部打点；可拖动、微调、把当前时间写入选中行、新增/编辑/确认/删除（可恢复）；显示当前时间附近逐字稿。时间轴上每个打点是一条竖线加小旗，竖线位置就是作答时间点。“以当前时间新增”会用该时刻前后各 N 秒（默认 10 秒，可调）的逐字稿让 AI 草拟问题和答案，生成期间已手动填写的内容不会被覆盖。
- **导出**：工作表“问题抽取”，五列“视频ID / 视频名称 / 问题时间点 / 问题 / 答案”，导出全部未删除行（有未确认行时提示），同时支持 UTF-8 BOM CSV；导出前校验并回读验证。
- **抽题规则（v2 结构化）**：A 基本设置（题量密度、时间点偏好，程序与 AI 共用）/ B 业务规则 / C 示例库 / D 专家细则 / E 系统内置（只读）。可预览实际发送给 AI 的内容；默认规则每次保存为新版本，可基于历史版本恢复。旧版纯文本规则仍可使用。
- **对象存储键**：`videos/<素材ID>/<随机批次号>/`，多个部署共用同一 COS 前缀也不会互相覆盖。

## 快捷键（审阅页）

| 键 | 作用 |
| --- | --- |
| 空格 | 播放 / 暂停 |
| ← / → | ±1 秒 |
| Shift + ← / → | ±0.1 秒 |
| ↑ / ↓ | 上 / 下一个打点（并定位播放） |
| Enter | 当前时间写入选中行 |
| N | 以当前时间新增打点 |
| C | 确认 / 取消确认 |

## 本地运行

需要 Python 3.9+、Node 20+、ffmpeg/ffprobe。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# .venv/bin/python -m playwright install chromium-headless-shell   # 扫码登录用（暂停使用）
npm ci
npm run build
cp -n .env.example .env && chmod 600 .env    # 填写 SECRET_KEY、APP_PASSWORD、AI_API_KEY、COS_*
.venv/bin/python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://127.0.0.1:8000`，输入 `APP_PASSWORD` 进入。

前端开发可另开 `npm run dev:web`（5173 端口，`/api` 代理到 8000）。`npm start` 等同于 `bash scripts/start.sh`。

测试：

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
```

## 部署

按 [docs/部署要求.md](docs/部署要求.md)。部署后台填写：

| 项 | 值 |
| --- | --- |
| 环境准备命令 | `bash scripts/prepare.sh` |
| 启动命令 | `bash scripts/start.sh 8916`（也接受 `--port 8916 --host 0.0.0.0` 形式；不写端口时用环境变量 `PORT`，再没有则 8000）。不要用 `vite`/`npm run dev`，那是前端开发服务器 |
| 健康检查 | `/api/health` |

`scripts/prepare.sh` 会检查 Python/Node/ffmpeg 版本、安装 Python 依赖（优先预编译包）、构建前端（无头浏览器安装已随扫码登录暂停）；会自动优先使用 `/usr/local/node24|22|20/bin` 下的 Node。

旧系统（glibc 2.17，如 CentOS 7）说明：`greenlet` 已固定在 `<3.3`，因为 3.3 起不再提供该系统的预编译包，源码编译会因 GCC 过旧失败。

额外注意：

- 生产设置 `APP_ENV=production`、`STORAGE_PROVIDER=cos`、`COOKIE_SECURE=true`，缺失时拒绝启动。
- **钉钉扫码登录暂停使用**：相关代码（`backend/ctp/login.py` 的调用、`playwright` 依赖、`prepare.sh` 中的浏览器安装）已注释，扫码会话接口已移除（可从提交 c2864a8 找回），目前只支持粘贴 Cookie。原说明：扫码登录依赖无头浏览器，要求 glibc ≥ 2.28。老系统（如 CentOS 7，glibc 2.17）无法运行，`prepare.sh` 会跳过安装，服务启动时自动检测，新建任务页默认切到“粘贴 Cookie”并给出获取步骤；其他功能不受影响。
- 部署机需能访问 `iot-admin.yukework.com`、`ips.yukework.com`、`login.dingtalk.com`、`ntresource.cdnjtzy.com` 和大模型代理。
- 单进程运行（后台任务与扫码会话在进程内）；不要启动多个 worker 进程共用同一数据库。
- 持久化并备份 `data/app.sqlite` 与 COS 前缀；COS 只保存 `videos/<id>/silences.json`、`transcription.json`。音频只在处理时临时生成、用完即删，不上传 COS（当前桶对 `.mp3` 配置了自动转码，上传会额外生成 HLS 文件并计费）。
- COS 前缀无需预先创建目录，首次上传自动出现。

## 数据与安全

- 内容生产系统 Cookie 只提取 `ZYBIPSCAS`，用 `SECRET_KEY` 派生密钥加密保存于任务，`CTP_COOKIE_TTL_HOURS`（默认 24 小时）后不再使用；不写日志、不返回前端。
- 网站访问使用共享口令，会话 Cookie 为 HttpOnly，登录接口限速；写操作校验同源。
- 部署平台验收与脚本调用可用 `ACCESS_TOKEN`：请求头 `Authorization: Bearer <ACCESS_TOKEN>`。未配置时只能通过口令登录访问。
- 视频本身不落盘也不上传，播放直接使用 CDN 地址。

## 目录

```text
backend/            FastAPI 应用
  main.py           API 与静态前端
  config.py         配置加载与启动校验
  db.py             数据模型（SQLite）
  ctp/              内容生产系统：扫码登录、素材解析
  pipeline/         媒体处理、ASR、抽题、后台任务
  export.py         Excel / CSV 导出与校验
  storage.py        COS / 本地存储
frontend/           Vue 3 + Vite
prompts/            默认抽题规则：default_v3.json（结构化，首次启动写入数据库；未改动过的旧内置 v2 会自动升级）、default_v2.json（上一版内置）、default_v1.md（旧版纯文本）
poc/                技术验证脚本
tests/              接口与核心逻辑测试
```
