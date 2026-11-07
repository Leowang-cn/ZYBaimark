from __future__ import annotations

import ipaddress
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db as dbm
from .config import ROOT, get_settings
from .ctp import client as ctp
from .ctp.login import login_manager
from .db import AppSession, DefaultPrompt, Generation, Mark, Task, Video, get_db, utcnow
from .export import ExportError, build_rows, to_csv, to_xlsx, validate_rows
from .pipeline.extract import fmt_ts
from .pipeline.worker import has_manual_edits, load_transcript, worker
from .security import check_password, decrypt, encrypt, hash_token, login_limiter, new_token

log = logging.getLogger("app")
SESSION_COOKIE = "mark_session"
SESSION_TTL = timedelta(days=7)
DEFAULT_PROMPT_FILE = ROOT / "prompts" / "default.md"
FRONTEND_DIST = ROOT / "frontend" / "dist"


def ensure_data_dir(path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        raise RuntimeError(f"数据目录 {path} 无法创建或不可写（{e.strerror}）。"
                           "请把 DATA_DIR 改为有写权限的路径，或删除 DATA_DIR 使用项目内 data/") from e
    log.info("数据目录：%s", path.resolve())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ensure_data_dir(get_settings().data_dir)
    dbm.init_db()
    with dbm.SessionLocal() as session:
        if not session.scalar(select(DefaultPrompt.id).limit(1)):
            session.add(DefaultPrompt(version=1, content=DEFAULT_PROMPT_FILE.read_text(encoding="utf-8")))
            session.commit()
    worker.start()
    yield
    worker.shutdown()


app = FastAPI(title="小数AI课自动打点", lifespan=lifespan, docs_url=None, redoc_url=None)


# ---------------- 鉴权 ----------------

def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    cidrs = [c.strip() for c in get_settings().trusted_proxy_cidrs.split(",") if c.strip()]
    fwd = request.headers.get("x-forwarded-for")
    try:
        if fwd and cidrs and any(ipaddress.ip_address(peer) in ipaddress.ip_network(c, strict=False) for c in cidrs):
            return fwd.split(",")[0].strip()
    except ValueError:
        pass
    return peer


def require_auth(request: Request, db: Session = Depends(get_db)) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        row = db.get(AppSession, hash_token(token))
        if row and row.expires_at > utcnow():
            return
    raise HTTPException(401, "请先登录")


def same_origin(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
        raise HTTPException(403, "跨站请求被拒绝")


Auth = [Depends(same_origin), Depends(require_auth)]


class LoginIn(BaseModel):
    password: str = Field(max_length=200)


@app.post("/api/auth/login", dependencies=[Depends(same_origin)])
def app_login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if not login_limiter.allow(client_ip(request)):
        raise HTTPException(429, "尝试过于频繁，请稍后再试")
    if not check_password(body.password):
        raise HTTPException(401, "口令错误")
    token = new_token()
    db.add(AppSession(token_hash=hash_token(token), expires_at=utcnow() + SESSION_TTL))
    db.query(AppSession).filter(AppSession.expires_at < utcnow()).delete()
    db.commit()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=get_settings().cookie_secure,
                        max_age=int(SESSION_TTL.total_seconds()))
    return {"ok": True}


@app.post("/api/auth/logout", dependencies=[Depends(same_origin)])
def app_logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.query(AppSession).filter(AppSession.token_hash == hash_token(token)).delete()
        db.commit()
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me", dependencies=Auth)
def me():
    return {"ok": True}


@app.get("/api/health")
def health():
    try:
        with dbm.SessionLocal() as session:
            session.execute(select(1))
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "error"}, status_code=503)
    return {"status": "ok"}


# ---------------- Prompt ----------------

class PromptIn(BaseModel):
    content: str = Field(min_length=20, max_length=60000)


def current_default(db: Session) -> DefaultPrompt:
    return db.scalars(select(DefaultPrompt).order_by(DefaultPrompt.version.desc()).limit(1)).first()


@app.get("/api/prompts/default", dependencies=Auth)
def get_default_prompt(db: Session = Depends(get_db)):
    p = current_default(db)
    return {"version": p.version, "content": p.content, "created_at": p.created_at.isoformat()}


@app.get("/api/prompts/default/versions", dependencies=Auth)
def default_versions(db: Session = Depends(get_db)):
    ps = db.scalars(select(DefaultPrompt).order_by(DefaultPrompt.version.desc())).all()
    return [{"version": p.version, "content": p.content, "created_at": p.created_at.isoformat()} for p in ps]


@app.put("/api/prompts/default", dependencies=Auth)
def save_default_prompt(body: PromptIn, db: Session = Depends(get_db)):
    cur = current_default(db)
    if cur.content.strip() == body.content.strip():
        return {"version": cur.version, "unchanged": True}
    p = DefaultPrompt(version=cur.version + 1, content=body.content)
    db.add(p)
    db.commit()
    return {"version": p.version}


# ---------------- 扫码登录 ----------------

@app.post("/api/login-sessions", dependencies=Auth)
def start_login():
    try:
        return login_manager.start().public()
    except RuntimeError as e:
        raise HTTPException(429, str(e)) from e


@app.get("/api/login-sessions/{sid}", dependencies=Auth)
def login_status(sid: str):
    sess = login_manager.get(sid)
    if not sess:
        raise HTTPException(404, "登录会话不存在")
    return sess.public()


@app.post("/api/login-sessions/{sid}/refresh", dependencies=Auth)
def login_refresh(sid: str):
    sess = login_manager.get(sid)
    if not sess or sess.status != "pending":
        raise HTTPException(409, "当前状态不能刷新二维码")
    sess.refresh_flag.set()
    return {"ok": True}


@app.delete("/api/login-sessions/{sid}", dependencies=Auth)
def login_cancel(sid: str):
    sess = login_manager.get(sid)
    if sess:
        sess.cancel_flag.set()
    return {"ok": True}


# ---------------- 任务 ----------------

class TaskIn(BaseModel):
    name: str = Field(default="", max_length=200)
    material_ids: list[str] = Field(min_length=1)
    prompt_text: str = Field(min_length=20, max_length=60000)
    login_session_id: str | None = None
    cookie_text: str | None = Field(default=None, max_length=20000)


def parse_ids(raw: list[str]) -> list[str]:
    ids: list[str] = []
    for item in raw:
        for x in re.split(r"[\s,，;；]+", item or ""):
            if not x:
                continue
            if not re.fullmatch(r"\d{6,25}", x):
                raise HTTPException(400, f"素材 ID 格式不正确：{x[:30]}")
            if x not in ids:
                ids.append(x)
    return ids


def video_summary(v: Video) -> dict:
    return {"id": v.id, "material_id": v.material_id, "name": v.name, "status": v.status, "stage": v.stage,
            "error": v.error, "duration_s": v.duration_s, "current_generation_id": v.current_generation_id,
            "generation_count": len(v.generations), "updated_at": v.updated_at.isoformat()}


@app.post("/api/tasks", dependencies=Auth)
def create_task(body: TaskIn, db: Session = Depends(get_db)):
    s = get_settings()
    ids = parse_ids(body.material_ids)
    if not ids:
        raise HTTPException(400, "请输入至少一个素材 ID")
    if len(ids) > s.max_ids_per_task:
        raise HTTPException(400, f"每个任务最多 {s.max_ids_per_task} 个素材 ID")
    cookie = None
    if body.login_session_id:
        cookie = login_manager.take_cookie(body.login_session_id)
        if not cookie:
            raise HTTPException(400, "扫码登录未完成或已使用，请重新扫码")
    elif body.cookie_text:
        cookie = ctp.extract_cookie(body.cookie_text)
        if not cookie:
            raise HTTPException(400, "粘贴内容中没有找到 ZYBIPSCAS，请确认已复制完整 Cookie")
    else:
        raise HTTPException(400, "请先扫码登录或粘贴 Cookie")
    try:
        ctp.check_login(cookie)
    except ctp.CtpAuthError as e:
        raise HTTPException(400, str(e)) from e
    except ctp.CtpError as e:
        raise HTTPException(502, str(e)) from e

    task = Task(name=body.name.strip() or f"任务 {utcnow():%m-%d %H:%M}", prompt_text=body.prompt_text,
                cookie_enc=encrypt(cookie), cookie_expire_at=utcnow() + timedelta(hours=s.cookie_ttl_hours))
    db.add(task)
    db.flush()
    for mid in ids:
        v = Video(task_id=task.id, material_id=mid)
        try:
            info = ctp.resolve_material(mid, cookie)
            v.name, v.url, v.encode_url, v.duration_s = info["name"], info["url"], info["encode_url"], info["duration_s"]
            v.status = "queued"
        except ctp.CtpAuthError as e:
            db.rollback()
            raise HTTPException(400, str(e)) from e
        except ctp.CtpError as e:
            v.status, v.error = "resolve_failed", str(e)
        db.add(v)
    db.commit()
    worker.notify()
    db.refresh(task)
    return {"id": task.id, "videos": [video_summary(v) for v in task.videos]}


@app.get("/api/tasks", dependencies=Auth)
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.scalars(select(Task).order_by(Task.id.desc()).limit(200)).all()
    out = []
    for t in tasks:
        st = [v.status for v in t.videos]
        out.append({"id": t.id, "name": t.name, "created_at": t.created_at.isoformat(), "total": len(st),
                    "done": st.count("done"), "failed": st.count("failed") + st.count("resolve_failed"),
                    "running": st.count("running") + st.count("queued")})
    return out


def get_task(db: Session, task_id: int) -> Task:
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t


@app.get("/api/tasks/{task_id}", dependencies=Auth)
def task_detail(task_id: int, db: Session = Depends(get_db)):
    t = get_task(db, task_id)
    return {"id": t.id, "name": t.name, "created_at": t.created_at.isoformat(), "prompt_text": t.prompt_text,
            "videos": [video_summary(v) for v in t.videos]}


class TaskRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@app.patch("/api/tasks/{task_id}", dependencies=Auth)
def rename_task(task_id: int, body: TaskRename, db: Session = Depends(get_db)):
    t = get_task(db, task_id)
    t.name = body.name.strip()
    db.commit()
    return {"ok": True}


# ---------------- 视频 / 生成版本 ----------------

def get_video(db: Session, video_id: int) -> Video:
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "视频不存在")
    return v


def mark_out(m: Mark) -> dict:
    return {"id": m.id, "time_s": m.time_s, "time": fmt_ts(m.time_s), "question": m.question, "answer": m.answer,
            "confirmed": m.confirmed, "deleted": m.deleted, "source": m.source, "ai_time_s": m.ai_time_s,
            "ai_question": m.ai_question, "ai_answer": m.ai_answer, "review_reason": m.review_reason,
            "evidence": m.evidence, "revision": m.revision}


def generation_out(g: Generation, with_marks: bool = True) -> dict:
    marks = [m for m in g.marks]
    d = {"id": g.id, "video_id": g.video_id, "version_no": g.version_no, "model": g.model, "asr_model": g.asr_model,
         "created_at": g.created_at.isoformat(), "target_count": g.target_count,
         "mark_count": sum(1 for m in marks if not m.deleted), "ai_count": sum(1 for m in marks if m.source == "ai"),
         "confirmed_count": sum(1 for m in marks if m.confirmed and not m.deleted),
         "has_manual_edits": has_manual_edits(g)}
    if with_marks:
        d["prompt_text"] = g.prompt_text
        d["marks"] = [mark_out(m) for m in sorted(marks, key=lambda m: (m.time_s, m.id))]
        d["excluded"] = json.loads(g.excluded_json or "[]")
    return d


@app.get("/api/videos/{video_id}", dependencies=Auth)
def video_detail(video_id: int, db: Session = Depends(get_db)):
    v = get_video(db, video_id)
    d = video_summary(v)
    d.update({"task_id": v.task_id, "task_name": v.task.name, "url": v.url, "encode_url": v.encode_url,
              "pending_prompt": v.pending_prompt,
              "generations": [generation_out(g, with_marks=False) for g in reversed(v.generations)]})
    return d


@app.get("/api/videos/{video_id}/transcript", dependencies=Auth)
def video_transcript(video_id: int, db: Session = Depends(get_db)):
    tr = load_transcript(get_video(db, video_id))
    if not tr:
        raise HTTPException(404, "逐字稿尚未生成")
    return {"segments": tr["segments"]}


class RegenIn(BaseModel):
    prompt_text: str = Field(min_length=20, max_length=60000)


def queue_regen(v: Video, prompt: str) -> None:
    if v.status in {"queued", "running"}:
        raise HTTPException(409, f"视频 {v.material_id} 正在处理中")
    if not v.url:
        raise HTTPException(409, f"视频 {v.material_id} 未解析成功，无法生成")
    v.pending_prompt, v.status, v.error, v.stage = prompt, "queued", "", ""


@app.post("/api/videos/{video_id}/generations", dependencies=Auth)
def regenerate(video_id: int, body: RegenIn, db: Session = Depends(get_db)):
    v = get_video(db, video_id)
    queue_regen(v, body.prompt_text)
    db.commit()
    worker.notify()
    return {"ok": True}


@app.post("/api/tasks/{task_id}/generations", dependencies=Auth)
def regenerate_task(task_id: int, body: RegenIn, db: Session = Depends(get_db)):
    t = get_task(db, task_id)
    n = 0
    for v in t.videos:
        if v.url and v.status not in {"queued", "running"}:
            queue_regen(v, body.prompt_text)
            n += 1
    db.commit()
    worker.notify()
    return {"queued": n}


@app.post("/api/videos/{video_id}/retry", dependencies=Auth)
def retry(video_id: int, db: Session = Depends(get_db)):
    v = get_video(db, video_id)
    if v.status != "failed":
        raise HTTPException(409, "只有失败的视频可以重试")
    v.status, v.error = "queued", ""
    db.commit()
    worker.notify()
    return {"ok": True}


class ReresolveIn(BaseModel):
    login_session_id: str | None = None
    cookie_text: str | None = Field(default=None, max_length=20000)


@app.post("/api/videos/{video_id}/resolve", dependencies=Auth)
def reresolve(video_id: int, body: ReresolveIn, db: Session = Depends(get_db)):
    v = get_video(db, video_id)
    if v.status != "resolve_failed":
        raise HTTPException(409, "只有解析失败的视频需要重新解析")
    cookie = None
    if body.login_session_id:
        cookie = login_manager.take_cookie(body.login_session_id)
    elif body.cookie_text:
        cookie = ctp.extract_cookie(body.cookie_text)
    elif v.task.cookie_enc and v.task.cookie_expire_at and v.task.cookie_expire_at > utcnow():
        cookie = decrypt(v.task.cookie_enc)
    if not cookie:
        raise HTTPException(400, "登录态已失效，请重新扫码登录")
    try:
        info = ctp.resolve_material(v.material_id, cookie)
    except ctp.CtpError as e:
        v.error = str(e)
        db.commit()
        raise HTTPException(400, str(e)) from e
    v.name, v.url, v.encode_url, v.duration_s = info["name"], info["url"], info["encode_url"], info["duration_s"]
    v.status, v.error = "queued", ""
    db.commit()
    worker.notify()
    return {"ok": True}


def get_generation(db: Session, gid: int) -> Generation:
    g = db.get(Generation, gid)
    if not g:
        raise HTTPException(404, "版本不存在")
    return g


@app.get("/api/generations/{gid}", dependencies=Auth)
def generation_detail(gid: int, db: Session = Depends(get_db)):
    return generation_out(get_generation(db, gid))


class CurrentIn(BaseModel):
    generation_id: int


@app.post("/api/videos/{video_id}/current-generation", dependencies=Auth)
def set_current(video_id: int, body: CurrentIn, db: Session = Depends(get_db)):
    v = get_video(db, video_id)
    g = get_generation(db, body.generation_id)
    if g.video_id != v.id:
        raise HTTPException(400, "版本不属于该视频")
    v.current_generation_id = g.id
    db.commit()
    return {"ok": True}


# ---------------- 打点 ----------------

class MarkIn(BaseModel):
    time_s: float = Field(ge=0)
    question: str = Field(default="", max_length=2000)
    answer: str = Field(default="", max_length=500)


class MarkPatch(BaseModel):
    revision: int
    time_s: float | None = Field(default=None, ge=0)
    question: str | None = Field(default=None, max_length=2000)
    answer: str | None = Field(default=None, max_length=500)
    confirmed: bool | None = None
    deleted: bool | None = None


def check_time(g: Generation, t: float) -> None:
    dur = g.video.duration_s
    if dur and t > dur + 0.001:
        raise HTTPException(400, "时间点超出视频时长")


@app.post("/api/generations/{gid}/marks", dependencies=Auth)
def add_mark(gid: int, body: MarkIn, db: Session = Depends(get_db)):
    g = get_generation(db, gid)
    check_time(g, body.time_s)
    m = Mark(generation_id=g.id, time_s=round(body.time_s, 3), question=body.question.strip(),
             answer=body.answer.strip(), source="manual")
    db.add(m)
    db.commit()
    return mark_out(m)


@app.patch("/api/marks/{mid}", dependencies=Auth)
def patch_mark(mid: int, body: MarkPatch, db: Session = Depends(get_db)):
    m = db.get(Mark, mid)
    if not m:
        raise HTTPException(404, "打点不存在")
    if m.revision != body.revision:
        return JSONResponse({"detail": "该行已被其他人修改，已为你刷新为最新内容", "mark": mark_out(m)}, status_code=409)
    if body.time_s is not None:
        check_time(m.generation, body.time_s)
        m.time_s = round(body.time_s, 3)
    if body.question is not None:
        m.question = body.question.strip()
    if body.answer is not None:
        m.answer = body.answer.strip()
    if body.confirmed is not None:
        m.confirmed = body.confirmed
    if body.deleted is not None:
        m.deleted = body.deleted
    m.revision += 1
    db.commit()
    return mark_out(m)


# ---------------- 导出 ----------------

@app.get("/api/export", dependencies=Auth)
def export(format: str = "xlsx", task_id: int | None = None, video_id: int | None = None,
           generation_id: int | None = None, db: Session = Depends(get_db)):
    if format not in {"xlsx", "csv"}:
        raise HTTPException(400, "format 只能是 xlsx 或 csv")
    items: list[tuple[str, str, list]] = []
    if generation_id:
        g = get_generation(db, generation_id)
        items.append((g.video.material_id, g.video.name, [m for m in g.marks if not m.deleted]))
        title = f"{g.video.material_id}_v{g.version_no}"
    elif video_id:
        v = get_video(db, video_id)
        g = db.get(Generation, v.current_generation_id) if v.current_generation_id else None
        if not g:
            raise HTTPException(400, "该视频还没有生成结果")
        items.append((v.material_id, v.name, [m for m in g.marks if not m.deleted]))
        title = v.material_id
    elif task_id:
        t = get_task(db, task_id)
        for v in t.videos:
            g = db.get(Generation, v.current_generation_id) if v.current_generation_id else None
            if g:
                items.append((v.material_id, v.name, [m for m in g.marks if not m.deleted]))
        if not items:
            raise HTTPException(400, "该任务还没有可导出的结果")
        title = t.name
    else:
        raise HTTPException(400, "请指定导出范围")
    rows = build_rows(items)
    try:
        validate_rows(rows)
        data = to_xlsx(rows) if format == "xlsx" else to_csv(rows)
    except ExportError as e:
        raise HTTPException(400, f"导出校验未通过：{e}") from e
    fname = re.sub(r"[\\/:*?\"<>|\s]+", "_", f"问题抽取_{title}")[:120] + f".{format}"
    mime = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if format == "xlsx"
            else "text/csv; charset=utf-8")
    return Response(data, media_type=mime,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


# ---------------- 前端 ----------------

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "前端尚未构建，请执行 npm run build"}, status_code=503)
    target = (FRONTEND_DIST / path).resolve()
    if path and target.is_file() and FRONTEND_DIST.resolve() in target.parents:
        return FileResponse(target)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})

