"""后台任务：每个视频依次执行 probe → audio → silence → asr → llm，各阶段产物缓存到对象存储。"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from .. import db as dbm
from ..config import get_settings
from ..db import Generation, Mark, Video
from ..storage import get_storage
from .extract import extract, finalize
from .media import PAUSE_MIN_S, PipelineError, detect_silences, extract_audio, probe, transcribe

log = logging.getLogger(__name__)


def _key(material_id: str, run_id: str, name: str) -> str:
    # 对象键必须全局唯一：多个部署（本地/服务器）可能共用同一 COS 前缀，而各自数据库的视频编号都从 1 开始
    return f"videos/{material_id}/{run_id}/{name}"


def has_manual_edits(gen: Generation | None) -> bool:
    return bool(gen) and any(m.edited for m in gen.marks)


def save_generation(session, video: Video, prompt: str, result: dict, rows: list[dict]) -> Generation:
    s = get_settings()
    no = (session.scalar(select(func.max(Generation.version_no)).where(Generation.video_id == video.id)) or 0) + 1
    gen = Generation(video_id=video.id, version_no=no, prompt_text=prompt, model=s.ai_model, asr_model=s.asr_model,
                     llm_raw_json=json.dumps(result["raw"], ensure_ascii=False),
                     excluded_json=json.dumps(result["excluded"], ensure_ascii=False),
                     target_count=result["target"])
    session.add(gen)
    session.flush()
    for r in rows:
        session.add(Mark(generation_id=gen.id, time_s=r["time"], question=r["question"], answer=r["answer"],
                         source="ai", ai_time_s=r["time"], ai_question=r["question"], ai_answer=r["answer"],
                         review_reason=r["review_reason"], evidence=r["evidence"]))
    current = session.get(Generation, video.current_generation_id) if video.current_generation_id else None
    # 当前版本已有人工修改时不自动切换，由用户决定
    if not has_manual_edits(current):
        video.current_generation_id = gen.id
    return gen


class Worker:
    def __init__(self):
        self.wake = threading.Event()
        self.stop_flag = threading.Event()
        self.running: set[int] = set()
        self.lock = threading.Lock()
        self.pool: ThreadPoolExecutor | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        with dbm.SessionLocal() as session:
            for v in session.scalars(select(Video).where(Video.status == "running")):
                v.status = "queued"  # 服务重启后续跑
            session.commit()
        self.pool = ThreadPoolExecutor(max_workers=get_settings().worker_concurrency, thread_name_prefix="video")
        self.thread = threading.Thread(target=self._loop, daemon=True, name="dispatcher")
        self.thread.start()

    def shutdown(self) -> None:
        self.stop_flag.set()
        self.wake.set()
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)

    def notify(self) -> None:
        self.wake.set()

    def _loop(self) -> None:
        cap = get_settings().worker_concurrency
        while not self.stop_flag.is_set():
            with self.lock:
                free = cap - len(self.running)
            if free > 0:
                with dbm.SessionLocal() as session:
                    ids = session.scalars(select(Video.id).where(Video.status == "queued")
                                          .order_by(Video.updated_at, Video.id).limit(free + len(self.running))).all()
                for vid in ids:
                    with self.lock:
                        if vid in self.running or len(self.running) >= cap:
                            continue
                        self.running.add(vid)
                    self.pool.submit(self._process_safe, vid)
            self.wake.wait(2)
            self.wake.clear()

    def _process_safe(self, video_id: int) -> None:
        try:
            self._process(video_id)
        except Exception as e:  # noqa: BLE001
            msg = str(e) if isinstance(e, PipelineError) else f"内部错误：{type(e).__name__}"
            if not isinstance(e, PipelineError):
                log.exception("处理视频 %s 失败", video_id)
            with dbm.SessionLocal() as session:
                v = session.get(Video, video_id)
                if v:
                    v.status, v.error = "failed", msg
                    session.commit()
        finally:
            with self.lock:
                self.running.discard(video_id)
            shutil.rmtree(get_settings().data_dir / "work" / str(video_id), ignore_errors=True)
            self.wake.set()

    def _set(self, video_id: int, **kw) -> None:
        with dbm.SessionLocal() as session:
            v = session.get(Video, video_id)
            for k, val in kw.items():
                setattr(v, k, val)
            session.commit()

    def _process(self, video_id: int) -> None:
        s = get_settings()
        store = get_storage()
        with dbm.SessionLocal() as session:
            v = session.get(Video, video_id)
            v.status, v.error = "running", ""
            session.commit()
            url, art = v.url, json.loads(v.artifacts_json or "{}")
            material_id = v.material_id
            prompt = v.pending_prompt or v.task.prompt_text
            duration = v.duration_s
        work = s.data_dir / "work" / str(video_id)
        work.mkdir(parents=True, exist_ok=True)

        run_id = art.get("run_id") or uuid.uuid4().hex[:12]
        art["run_id"] = run_id

        def save_art(**kw):
            art.update(kw)
            self._set(video_id, artifacts_json=json.dumps(art))

        if not art.get("probed"):
            self._set(video_id, stage="probe")
            info = probe(url)
            duration = info["duration"]
            if duration > s.max_video_seconds:
                raise PipelineError(f"视频时长 {duration:.0f} 秒超过上限 {s.max_video_seconds} 秒")
            self._set(video_id, duration_s=duration)
            save_art(probed=True)

        need_asr = not art.get("transcription")
        # 停顿阈值调整后，按旧阈值缓存的静音结果需要重新检测
        need_sil = not art.get("silences") or art.get("silence_min_s", 0.5) != PAUSE_MIN_S
        if need_asr or need_sil:
            wav = work / "audio.wav"
            # 音频只作中间产物，不上传 COS：该桶对 mp3 有自动转码规则，会产生额外文件与费用
            self._set(video_id, stage="audio")
            extract_audio(url, wav)
            if need_sil:
                self._set(video_id, stage="silence")
                sil = detect_silences(wav)
                store.put_bytes(_key(material_id, run_id, "silences.json"), json.dumps(sil).encode(), "application/json")
                save_art(silences=_key(material_id, run_id, "silences.json"), silence_min_s=PAUSE_MIN_S)
            if need_asr:
                self._set(video_id, stage="asr")
                tr = transcribe(wav, duration, work)
                store.put_bytes(_key(material_id, run_id, "transcription.json"),
                                json.dumps(tr, ensure_ascii=False).encode(), "application/json")
                save_art(transcription=_key(material_id, run_id, "transcription.json"))

        tr = json.loads(store.get_bytes(art["transcription"]))
        sil = json.loads(store.get_bytes(art["silences"]))
        result = extract(prompt, tr, sil, duration, progress=lambda st: self._set(video_id, stage=st))
        self._set(video_id, stage="finalize")
        rows = finalize(result["items"], tr, sil, duration, prompt)
        with dbm.SessionLocal() as session:
            v = session.get(Video, video_id)
            save_generation(session, v, prompt, result, rows)
            v.status, v.stage, v.error, v.pending_prompt = "done", "", "", None
            session.commit()
        log.info("视频 %s 完成，%d 题", video_id, len(rows))


worker = Worker()


def load_transcript(video: Video) -> dict | None:
    art = json.loads(video.artifacts_json or "{}")
    if not art.get("transcription"):
        return None
    try:
        return json.loads(get_storage().get_bytes(art["transcription"]))
    except Exception:  # noqa: BLE001
        return None

