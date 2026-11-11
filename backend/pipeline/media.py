from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

import requests

from ..config import get_settings

ASR_CHUNK_S = 20 * 60
PAUSE_MIN_S = 0.3


class PipelineError(Exception):
    pass


def _run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise PipelineError(f"找不到命令 {cmd[0]}，请安装 ffmpeg") from e
    except subprocess.TimeoutExpired as e:
        raise PipelineError(f"{Path(cmd[0]).name} 执行超时") from e


def probe(src: str) -> dict:
    s = get_settings()
    r = _run([s.ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", src],
             timeout=120)
    if r.returncode != 0:
        raise PipelineError("无法读取视频（ffprobe 失败）")
    j = json.loads(r.stdout or "{}")
    types = {st.get("codec_type") for st in j.get("streams", [])}
    if "audio" not in types:
        raise PipelineError("视频没有音轨")
    return {"duration": float(j["format"]["duration"]), "has_video": "video" in types}


def extract_audio(url: str, wav: Path) -> None:
    s = get_settings()
    r = _run([s.ffmpeg, "-y", "-v", "error", "-i", url, "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    if r.returncode != 0 or not wav.exists():
        raise PipelineError("抽取音频失败：" + (r.stderr or "")[-300:])


def to_mp3(wav: Path, mp3: Path, start: float = 0, length: float | None = None) -> None:
    s = get_settings()
    cmd = [s.ffmpeg, "-y", "-v", "error", "-ss", str(start)]
    if length:
        cmd += ["-t", str(length)]
    cmd += ["-i", str(wav), "-ac", "1", "-ar", "16000", "-b:a", "48k", str(mp3)]
    if _run(cmd).returncode != 0:
        raise PipelineError("音频转码失败")


def detect_silences(wav: Path, min_s: float = PAUSE_MIN_S) -> list[list[float]]:
    s = get_settings()
    vol = _run([s.ffmpeg, "-v", "info", "-i", str(wav), "-af", "volumedetect", "-f", "null", "-"]).stderr
    m = re.search(r"mean_volume: (-?[\d.]+) dB", vol)
    noise_db = max(float(m.group(1)) - 15, -45) if m else -35
    err = _run([s.ffmpeg, "-v", "info", "-i", str(wav), "-af", f"silencedetect=n={noise_db}dB:d={min_s}",
                "-f", "null", "-"]).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return [[a, b] for a, b in zip(starts, ends)]


def transcribe(wav: Path, duration: float, work: Path) -> dict:
    s = get_settings()
    if not s.asr_api_key:
        raise PipelineError("缺少 ASR_API_KEY")
    segments, words = [], []
    n = max(1, math.ceil(duration / ASR_CHUNK_S))
    for i in range(n):
        off = i * ASR_CHUNK_S
        mp3 = work / f"chunk{i}.mp3"
        to_mp3(wav, mp3, off, ASR_CHUNK_S)
        try:
            with mp3.open("rb") as f:
                r = requests.post(
                    f"{s.asr_base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {s.asr_api_key}"},
                    data=[("model", s.asr_model), ("language", "zh"), ("response_format", "verbose_json"),
                          ("timestamp_granularities[]", "segment"), ("timestamp_granularities[]", "word"),
                          ("prompt", "以下是小学数学课堂的老师讲课录音，使用简体中文。")],
                    files={"file": (mp3.name, f, "audio/mpeg")}, timeout=600)
        except requests.RequestException as e:
            raise PipelineError(f"ASR 请求失败：{type(e).__name__}") from e
        finally:
            mp3.unlink(missing_ok=True)
        if r.status_code != 200:
            raise PipelineError(f"ASR 返回 HTTP {r.status_code}")
        j = r.json()
        if not j.get("segments"):
            raise PipelineError("ASR 未返回分段时间戳")
        for seg in j["segments"]:
            segments.append({"start": seg["start"] + off, "end": seg["end"] + off, "text": seg["text"].strip()})
        for w in j.get("words") or []:
            words.append({"start": w["start"] + off, "end": w["end"] + off, "word": w["word"]})
    for i, seg in enumerate(segments):
        seg["id"] = f"s{i + 1}"
    return {"segments": segments, "words": words, "model": s.asr_model}
