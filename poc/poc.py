"""技术验证：素材ID → 视频URL → 音频 → ASR(词级时间戳) → LLM 抽题 → Excel/CSV。

用法：
  .venv/bin/python poc/poc.py login                # 钉钉扫码登录，Cookie 存 poc/out/cookie.txt
  .venv/bin/python poc/poc.py resolve <素材ID>      # 只验证 ID → 视频 URL
  .venv/bin/python poc/poc.py run <素材ID> [...]    # 跑全流程
  可用 --cookie-file 指定手动粘贴的 Cookie（方案 B 兜底）。
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
OUT.mkdir(exist_ok=True)
load_dotenv(ROOT.parent / ".env")

SITE = "https://iot-admin.yukework.com"
LIST_API = f"{SITE}/iot-content-backend/backend/resource/list"
LOGIN_ENTRY = f"{SITE}/fe-ctpsystem/"
VIDEO_TYPE = 2
MAX_DURATION_S = 40 * 60
ASR_CHUNK_S = 20 * 60
PAUSE_MIN_S = 0.5
INSTRUCTION_WORDS = ["选一选", "算一算", "填一填", "判断", "回答", "圈一圈", "连一连", "找一找", "想一想",
                     "写一写", "计算一下", "选择一下", "告诉我", "数一数", "比一比", "说一说"]

AI_BASE_URL = os.getenv("AI_BASE_URL", "https://llm.baifentan.com/openproxy/rp/v1").rstrip("/")
AI_MODEL = os.getenv("AI_MODEL", "gpt-5.2")
AI_API_KEY = os.getenv("AI_API_KEY", "")
ASR_BASE_URL = (os.getenv("ASR_BASE_URL") or AI_BASE_URL).rstrip("/")
ASR_MODEL = os.getenv("ASR_MODEL", "whisper-1")
ASR_API_KEY = os.getenv("ASR_API_KEY") or AI_API_KEY
FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.getenv("FFPROBE_BIN", "ffprobe")


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


# ---------------- 登录 ----------------

def login_qr(cookie_path: Path, timeout_s: int = 180) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.goto(LOGIN_ENTRY, wait_until="networkidle", timeout=30000)
        page.get_by_text("钉钉扫码").click()
        page.frame_locator("iframe[src*='login.dingtalk.com']").locator("canvas").first.wait_for(
            state="attached", timeout=15000)
        page.wait_for_timeout(2000)
        qr_png = OUT / "login_qr.png"
        page.locator("iframe[src*='login.dingtalk.com']").screenshot(path=str(qr_png))
        log(f"请用钉钉扫描二维码：{qr_png}")
        subprocess.run(["open", str(qr_png)], check=False)
        deadline = time.time() + timeout_s
        token = None
        last_url = page.url
        while time.time() < deadline:
            token = next((c["value"] for c in ctx.cookies() if c["name"] == "ZYBIPSCAS"), None)
            if token:
                break
            if page.url != last_url:
                last_url = page.url
                log(f"页面跳转：{last_url[:80]}")
            page.wait_for_timeout(1500)
        if not token:
            page.screenshot(path=str(OUT / "login_timeout.png"))
        browser.close()
    if not token:
        raise SystemExit("扫码超时，未获取到登录 Cookie")
    cookie_path.write_text(f"ZYBIPSCAS={token}")
    os.chmod(cookie_path, 0o600)
    log("登录成功，Cookie 已保存（仅本地，权限 600）")


def load_cookie(path: Path) -> str:
    raw = path.read_text().strip()
    m = re.search(r"ZYBIPSCAS=([^;\s'\"]+)", raw)
    if not m:
        raise SystemExit("Cookie 中未找到 ZYBIPSCAS")
    return f"ZYBIPSCAS={m.group(1)}"


def site_post(url: str, body: dict, cookie: str) -> dict:
    r = requests.post(url, json=body, headers={"Cookie": cookie}, timeout=20)
    r.raise_for_status()
    j = r.json()
    if j.get("errNo") == 200002:
        raise SystemExit("登录已失效，请重新登录")
    if j.get("errNo") != 0:
        raise SystemExit(f"接口错误 {j.get('errNo')}: {j.get('errMsg')}")
    return j["data"]


def resolve_material(material_id: str, cookie: str) -> dict:
    data = site_post(LIST_API, {"subject": 0, "resourceType": 0, "knowledgeId": "0", "keyword": material_id,
                                "searchCategory": 0, "noTag": 0, "phase": 0, "treeSubject": 0, "pageNum": 1,
                                "pageSize": 10, "auditStatus": 0, "publishTarget": 1}, cookie)
    hit = next((x for x in data.get("list", []) if x.get("resourceId") == material_id), None)
    if not hit:
        raise SystemExit(f"未找到素材 {material_id}")
    if hit.get("resourceType") != VIDEO_TYPE:
        raise SystemExit(f"素材 {material_id} 不是视频（{hit.get('resourceTypeName')}）")
    if not hit.get("resourceUrl"):
        raise SystemExit(f"素材 {material_id} 无视频地址")
    return {"id": material_id, "name": hit.get("realName") or material_id, "url": hit["resourceUrl"],
            "duration_ms": hit.get("resourceDuration") or 0}


# ---------------- 媒体 ----------------

def probe_duration(src: str) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", src],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def extract_audio(url: str, wav: Path) -> None:
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", url, "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)


def detect_silences(wav: Path, min_s: float = 0.5) -> list[tuple[float, float]]:
    vol = subprocess.run([FFMPEG, "-v", "info", "-i", str(wav), "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.search(r"mean_volume: (-?[\d.]+) dB", vol)
    noise_db = max(float(m.group(1)) - 15, -45) if m else -35
    err = subprocess.run([FFMPEG, "-v", "info", "-i", str(wav), "-af", f"silencedetect=n={noise_db}dB:d={min_s}",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends))


# ---------------- ASR ----------------

def transcribe(wav: Path, duration: float, work: Path) -> dict:
    if not ASR_API_KEY:
        raise SystemExit("缺少 ASR_API_KEY/AI_API_KEY，请在 .env 配置")
    segments, words = [], []
    n = max(1, math.ceil(duration / ASR_CHUNK_S))
    for i in range(n):
        off = i * ASR_CHUNK_S
        mp3 = work / f"chunk{i}.mp3"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", str(off), "-t", str(ASR_CHUNK_S), "-i", str(wav),
                        "-ac", "1", "-ar", "16000", "-b:a", "48k", str(mp3)], check=True)
        log(f"ASR 第 {i + 1}/{n} 片")
        with mp3.open("rb") as f:
            r = requests.post(f"{ASR_BASE_URL}/audio/transcriptions",
                              headers={"Authorization": f"Bearer {ASR_API_KEY}"},
                              data=[("model", ASR_MODEL), ("language", "zh"), ("response_format", "verbose_json"),
                                    ("timestamp_granularities[]", "segment"), ("timestamp_granularities[]", "word"),
                                    ("prompt", "以下是小学数学课堂的老师讲课录音，使用简体中文。")],
                              files={"file": (mp3.name, f, "audio/mpeg")}, timeout=600)
        r.raise_for_status()
        j = r.json()
        if not j.get("segments"):
            raise SystemExit("ASR 未返回分段时间戳")
        for s in j["segments"]:
            segments.append({"start": s["start"] + off, "end": s["end"] + off, "text": s["text"].strip()})
        for w in j.get("words") or []:
            words.append({"start": w["start"] + off, "end": w["end"] + off, "word": w["word"]})
    for i, s in enumerate(segments):
        s["id"] = f"s{i + 1}"
    return {"segments": segments, "words": words}


# ---------------- 证据稿 ----------------

def fmt_ts(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def build_evidence(tr: dict, silences: list[tuple[float, float]]) -> str:
    lines = []
    segs = tr["segments"]
    for i, s in enumerate(segs):
        lines.append(f"[{s['id']}] {fmt_ts(s['start'])}-{fmt_ts(s['end'])} {s['text']}")
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else math.inf
        for a, b in silences:
            if s["end"] - 0.4 <= a < nxt and b - a >= PAUSE_MIN_S:
                lines.append(f"    <停顿 {b - a:.1f}s>")
                break
    return "\n".join(lines)


# ---------------- LLM ----------------

DEFAULT_PROMPT = ROOT.parent / "prompts" / "default.md"

# 系统固定的输入/输出契约，不对用户暴露，保证人工修改规则后程序仍能解析结果
CONTRACT = """
【系统约定（优先级高于以上规则中的输出格式要求）】
- 输入是带分段编号和时间的逐字稿（ASR 可能有错字），"<停顿 Xs>" 表示该分段后检测到的静音。
- 你不直接输出时间，只输出时间锚点，由程序换算为 HH:MM:SS.mmm：
  anchor_segment：锚点所在分段编号；
  anchor_text：该分段中锚点处最后说出的 2-8 个字，必须是该分段文本中连续出现的原文。
- answer_evidence 填老师公布或验证答案的原话；答案无法确定时 needs_review=true 并在 review_reason 说明。
- 不要输出 Excel/CSV/Markdown 表格，只输出 JSON。
"""

ITEM_SCHEMA = ('{"question":"","answer":"","anchor_segment":"s12","anchor_text":"","answer_segment":"s14",'
               '"answer_evidence":"","needs_review":false,"review_reason":""}')

PASS1_TASK = ("本轮任务：第一轮候选发现与筛选。只输出 JSON：\n"
              '{"items":[' + ITEM_SCHEMA + '],"excluded":[{"segment":"s3","text":"","reason":""}]}\n'
              "items 按时间顺序；excluded 列出判断过但排除的疑似问题/指令。")
PASS2_TASK = ("本轮任务：漏题回扫。下方给出已收录题目和程序找到的尚未判断的互动指令/长停顿位置，逐个判断是否新增。\n"
              "目标题量约 {target} 题，当前 {current} 题；不得为凑数收录不合格问题。只输出 JSON：\n"
              '{{"add":[' + ITEM_SCHEMA.replace("{", "{{").replace("}", "}}") + '],"excluded":[{{"segment":"","text":"","reason":""}}]}}')
PASS3_TASK = ("本轮任务：逐题质量复核与反向核验，可修改、删除、合并。只输出 JSON：\n"
              '{"items":[' + ITEM_SCHEMA + ']}')


def system_prompt(rules: str, task: str) -> str:
    return f"{rules.strip()}\n{CONTRACT}\n{task}"


def chat_json(system: str, user: str) -> dict:
    if not AI_API_KEY:
        raise SystemExit("缺少 AI_API_KEY，请在 .env 配置")
    r = requests.post(f"{AI_BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {AI_API_KEY}"},
                      json={"model": AI_MODEL, "response_format": {"type": "json_object"},
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                      timeout=300)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    return json.loads(m.group(0) if m else content)


def uncovered_hints(tr: dict, silences, items: list[dict], excluded: list[dict]) -> list[str]:
    seen = {i.get("anchor_segment") for i in items} | {e.get("segment") for e in excluded}
    idx = {s["id"]: k for k, s in enumerate(tr["segments"])}
    near = set()
    for sid in seen:
        if sid in idx:
            near.update(range(idx[sid] - 2, idx[sid] + 3))
    hints = []
    for k, s in enumerate(tr["segments"]):
        if k in near:
            continue
        kw = [w for w in INSTRUCTION_WORDS if w in s["text"]]
        long_pause = any(s["end"] - 0.4 <= a <= s["end"] + 0.8 and b - a >= 1.0 for a, b in silences)
        if kw or (long_pause and re.search(r"[?？吗呢多少几什么哪]", s["text"])):
            hints.append(f"{s['id']} {s['text']}" + (f"（指令:{'/'.join(kw)}）" if kw else "（问句+长停顿）"))
    return hints


# ---------------- 时间计算与校验 ----------------

def _norm(t: str) -> str:
    return re.sub(r"[\s，。！？、,.!?：:“”\"'（）()]", "", t)


def resolve_anchor(item: dict, tr: dict, silences) -> tuple[float | None, str]:
    seg = next((s for s in tr["segments"] if s["id"] == item.get("anchor_segment")), None)
    if not seg:
        return None, "锚点分段不存在"
    anchor = _norm(item.get("anchor_text", ""))
    t_end = seg["end"]
    note = ""
    ws = [w for w in tr["words"] if seg["start"] - 0.05 <= w["start"] <= seg["end"] + 0.02]
    if ws and anchor:
        joined, spans = "", []
        for w in ws:
            for ch in _norm(w["word"]):
                joined += ch
                spans.append(w["end"])
        pos = joined.rfind(anchor)
        if pos >= 0:
            t_end = spans[pos + len(anchor) - 1]
        else:
            note = "锚点文字未精确匹配，使用分段结尾"
    elif not ws:
        note = "无词级时间戳，使用分段结尾"
    # 吸附到紧邻的静音起点，修正 ASR 词边界偏移
    for a, _b in silences:
        if t_end - 0.3 <= a <= t_end + 0.8:
            return a, note
    return t_end, note


def normalize_answer(a: str) -> str:
    a = a.strip()
    m = re.fullmatch(r"(?:选|答案是|正确答案是)?\s*([A-Ha-h])\s*(?:选项|项)?(?:[（(].*[）)]|[，,].*)?", a)
    return m.group(1).upper() if m else a


def text_support(question: str, tr: dict, anchor_seg: str) -> float:
    idx = next((k for k, s in enumerate(tr["segments"]) if s["id"] == anchor_seg), None)
    if idx is None:
        return 0.0
    window = _norm("".join(s["text"] for s in tr["segments"][max(0, idx - 4): idx + 2]))
    q = _norm(question)
    if not q:
        return 0.0
    sm = difflib.SequenceMatcher(None, q, window, autojunk=False)
    return sum(b.size for b in sm.get_matching_blocks()) / len(q)


def finalize(items: list[dict], tr: dict, silences, duration: float) -> list[dict]:
    rows = []
    for it in items:
        t, note = resolve_anchor(it, tr, silences)
        reasons = [r for r in [it.get("review_reason", ""), note] if r]
        if t is None or not (0 <= t <= duration):
            reasons.append("时间点无效")
            t = t or 0.0
        sil = next((b - a for a, b in silences if abs(a - t) <= 0.15), None)
        gap = sil if sil is not None else next((w["start"] - t for w in tr["words"] if w["start"] >= t + 0.1), math.inf)
        if gap < 0.3:
            reasons.append(f"锚点后几乎无停顿({gap:.1f}s)，可能无作答机会")
        sup = text_support(it.get("question", ""), tr, it.get("anchor_segment", ""))
        if sup < 0.7:
            reasons.append(f"问题原话匹配度低({sup:.0%})")
        ans = normalize_answer(it.get("answer", ""))
        if not it.get("question") or not ans:
            reasons.append("问题或答案为空")
        rows.append({"time": t, "question": it.get("question", "").strip(), "answer": ans,
                     "needs_review": bool(it.get("needs_review")) or bool(reasons),
                     "review_reason": "；".join(reasons), "evidence": it.get("answer_evidence", ""),
                     "anchor_segment": it.get("anchor_segment")})
    rows.sort(key=lambda r: r["time"])
    dedup = []
    for r in rows:
        if dedup and abs(r["time"] - dedup[-1]["time"]) < 2.0:
            dedup[-1]["needs_review"] = True
            dedup[-1]["review_reason"] += "；与相邻题时间过近，可能重复"
            continue
        dedup.append(r)
    return dedup


def export(rows_by_video: list[tuple[dict, list[dict]]], base: Path) -> None:
    from openpyxl import Workbook, load_workbook

    header = ["音频名称", "问题时间点", "问题", "答案"]
    data = [[f"{m['id']} {m['name']}", fmt_ts(r["time"]), r["question"], r["answer"]]
            for m, rows in rows_by_video for r in rows]
    wb = Workbook()
    ws = wb.active
    ws.title = "问题抽取"
    ws.append(header)
    for row in data:
        ws.append(row)
    wb.save(base.with_suffix(".xlsx"))
    with base.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([header, *data])
    back = list(load_workbook(base.with_suffix(".xlsx"))["问题抽取"].iter_rows(values_only=True))
    assert list(back[0]) == header and len(back) == len(data) + 1
    assert all(re.fullmatch(r"\d{2}:\d{2}:\d{2}\.\d{3}", r[1]) for r in data)
    assert not any(re.search(r"[A-H]\s*选项|[A-H][（(]", r[3]) for r in data)


# ---------------- 主流程 ----------------

def run_one(material_id: str, cookie: str, rules: str, prompt_label: str) -> tuple[dict, list[dict]]:
    work = OUT / material_id
    work.mkdir(exist_ok=True)
    meta = resolve_material(material_id, cookie)
    log(f"素材：{meta['name']}")
    duration = probe_duration(meta["url"])
    if duration > MAX_DURATION_S:
        raise SystemExit(f"视频时长 {duration:.0f}s 超过上限")
    log(f"时长 {duration:.1f}s，抽取音频")
    wav = work / "audio.wav"
    if not wav.exists():
        extract_audio(meta["url"], wav)
    silences = detect_silences(wav)
    log(f"检测到 {len(silences)} 处停顿")
    tr_path = work / "transcription.json"
    if tr_path.exists():
        tr = json.loads(tr_path.read_text())
    else:
        tr = transcribe(wav, duration, work)
        tr_path.write_text(json.dumps(tr, ensure_ascii=False, indent=1))
    log(f"ASR 分段 {len(tr['segments'])}，词 {len(tr['words'])}")
    evidence = build_evidence(tr, silences)
    (work / "evidence.txt").write_text(evidence)
    target = math.ceil(duration / 60)

    log("LLM 第 1 轮：候选发现")
    p1 = chat_json(system_prompt(rules, PASS1_TASK), evidence)
    items, excluded = p1.get("items", []), p1.get("excluded", [])
    hints = uncovered_hints(tr, silences, items, excluded)
    log(f"第 1 轮 {len(items)} 题；未覆盖线索 {len(hints)} 处")
    if hints or len(items) < target:
        log("LLM 第 2 轮：漏题回扫")
        user = (f"逐字稿：\n{evidence}\n\n已收录：\n{json.dumps(items, ensure_ascii=False)}\n\n"
                f"待判断位置：\n" + ("\n".join(hints) or "（无，但题量不足，请完整回扫）"))
        p2 = chat_json(system_prompt(rules, PASS2_TASK.format(target=target, current=len(items))), user)
        items += p2.get("add", [])
        excluded += p2.get("excluded", [])
    log("LLM 第 3 轮：逐题复核")
    p3 = chat_json(system_prompt(rules, PASS3_TASK), f"逐字稿：\n{evidence}\n\n待复核题目：\n{json.dumps(items, ensure_ascii=False)}")
    items = p3.get("items", items)
    rows = finalize(items, tr, silences, duration)
    vdir = work / "versions"
    vdir.mkdir(exist_ok=True)
    ver = len(list(vdir.glob("v*.json"))) + 1
    out = vdir / f"v{ver}.json"
    out.write_text(json.dumps({"version": ver, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "prompt_label": prompt_label, "prompt": rules, "model": AI_MODEL,
                               "asr_model": ASR_MODEL, "meta": meta, "duration": duration, "target": target,
                               "rows": rows, "excluded": excluded, "llm_raw": {"p1": p1, "p3": p3}},
                              ensure_ascii=False, indent=1))
    review = sum(r["needs_review"] for r in rows)
    log(f"完成 v{ver}：{len(rows)} 题（目标 {target}），待复核 {review} → {out}")
    return meta, rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["login", "run", "resolve"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--cookie-file", default=str(OUT / "cookie.txt"))
    ap.add_argument("--prompt", default=str(DEFAULT_PROMPT), help="抽题规则文件，默认 prompts/default.md")
    a = ap.parse_args()
    cookie_path = Path(a.cookie_file)
    if a.cmd == "login":
        login_qr(cookie_path)
        return
    cookie = load_cookie(cookie_path)
    if a.cmd == "resolve":
        for i in a.ids:
            print(json.dumps(resolve_material(i, cookie), ensure_ascii=False))
        return
    prompt_path = Path(a.prompt)
    rules = prompt_path.read_text()
    results = [run_one(i, cookie, rules, prompt_path.name) for i in a.ids]
    base = OUT / f"问题抽取_{time.strftime('%Y%m%d_%H%M%S')}"
    export(results, base)
    log(f"已导出 {base}.xlsx / .csv")


if __name__ == "__main__":
    sys.exit(main())
