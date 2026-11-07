from __future__ import annotations

import difflib
import json
import math
import re

import requests

from ..config import get_settings
from .media import PAUSE_MIN_S, PipelineError

INSTRUCTION_WORDS = ["选一选", "算一算", "填一填", "判断", "回答", "圈一圈", "连一连", "找一找", "想一想",
                     "写一写", "计算一下", "选择一下", "告诉我", "数一数", "比一比", "说一说"]

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
              '{{"add":[' + ITEM_SCHEMA.replace("{", "{{").replace("}", "}}")
              + '],"excluded":[{{"segment":"","text":"","reason":""}}]}}')
PASS3_TASK = "本轮任务：逐题质量复核与反向核验，可修改、删除、合并。只输出 JSON：\n" '{"items":[' + ITEM_SCHEMA + ']}'


def system_prompt(rules: str, task: str) -> str:
    return f"{rules.strip()}\n{CONTRACT}\n{task}"


def fmt_ts(t: float) -> str:
    ms = int(round(max(t, 0) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def parse_ts(text: str) -> float:
    m = re.fullmatch(r"\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\s*", text or "")
    if not m:
        raise ValueError("时间格式应为 HH:MM:SS.mmm")
    h, mi, s, ms = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(s) + int((ms or "0").ljust(3, "0")) / 1000


def build_evidence(tr: dict, silences) -> str:
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


def chat_json(system: str, user: str, timeout: int = 300) -> dict:
    s = get_settings()
    try:
        r = requests.post(f"{s.ai_base_url}/chat/completions", headers={"Authorization": f"Bearer {s.ai_api_key}"},
                          json={"model": s.ai_model, "response_format": {"type": "json_object"},
                                "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": user}]},
                          timeout=timeout)
    except requests.RequestException as e:
        raise PipelineError(f"大模型请求失败：{type(e).__name__}") from e
    if r.status_code != 200:
        raise PipelineError(f"大模型返回 HTTP {r.status_code}")
    content = r.json()["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", content, re.S)
    try:
        return json.loads(m.group(0) if m else content)
    except json.JSONDecodeError as e:
        raise PipelineError("大模型输出不是合法 JSON") from e


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


def extract(rules: str, tr: dict, silences, duration: float, progress=lambda _m: None) -> dict:
    evidence = build_evidence(tr, silences)
    target = math.ceil(duration / 60)
    progress("llm1")
    p1 = chat_json(system_prompt(rules, PASS1_TASK), evidence)
    items, excluded = list(p1.get("items") or []), list(p1.get("excluded") or [])
    hints = uncovered_hints(tr, silences, items, excluded)
    p2 = {}
    if hints or len(items) < target:
        progress("llm2")
        user = (f"逐字稿：\n{evidence}\n\n已收录：\n{json.dumps(items, ensure_ascii=False)}\n\n待判断位置：\n"
                + ("\n".join(hints) or "（无，但题量不足，请完整回扫）"))
        p2 = chat_json(system_prompt(rules, PASS2_TASK.format(target=target, current=len(items))), user)
        items += p2.get("add") or []
        excluded += p2.get("excluded") or []
    progress("llm3")
    p3 = chat_json(system_prompt(rules, PASS3_TASK),
                   f"逐字稿：\n{evidence}\n\n待复核题目：\n{json.dumps(items, ensure_ascii=False)}")
    final = p3.get("items")
    if not isinstance(final, list):
        final = items
    return {"items": final, "excluded": excluded, "target": target, "raw": {"p1": p1, "p2": p2, "p3": p3}}


# ---------------- 时间计算与校验 ----------------

def _norm(t: str) -> str:
    return re.sub(r"[\s，。！？、,.!?：:“”\"'（）()]", "", t or "")


def resolve_anchor(item: dict, tr: dict, silences) -> tuple[float | None, str]:
    seg = next((s for s in tr["segments"] if s["id"] == item.get("anchor_segment")), None)
    if not seg:
        return None, "锚点分段不存在"
    anchor = _norm(item.get("anchor_text", ""))
    t_end, note = seg["end"], ""
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
    a = (a or "").strip()
    m = re.fullmatch(r"(?:选|答案是|正确答案是)?\s*([A-Ha-h])\s*(?:选项|项)?(?:[（(].*[）)]|[，,].*)?", a)
    return m.group(1).upper() if m else a


def text_support(question: str, tr: dict, anchor_seg: str) -> float:
    idx = next((k for k, s in enumerate(tr["segments"]) if s["id"] == anchor_seg), None)
    q = _norm(question)
    if idx is None or not q:
        return 0.0
    window = _norm("".join(s["text"] for s in tr["segments"][max(0, idx - 4): idx + 2]))
    sm = difflib.SequenceMatcher(None, q, window, autojunk=False)
    return sum(b.size for b in sm.get_matching_blocks()) / len(q)


def finalize(items: list[dict], tr: dict, silences, duration: float) -> list[dict]:
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t, note = resolve_anchor(it, tr, silences)
        reasons = [r for r in [str(it.get("review_reason") or ""), note] if r]
        if t is None or not (0 <= t <= duration):
            reasons.append("时间点无效")
            t = min(max(t or 0.0, 0.0), duration)
        sil = next((b - a for a, b in silences if abs(a - t) <= 0.15), None)
        gap = sil if sil is not None else next((w["start"] - t for w in tr["words"] if w["start"] >= t + 0.1),
                                               math.inf)
        if gap < 0.3:
            reasons.append(f"锚点后几乎无停顿({gap:.1f}s)，可能无作答机会")
        question = str(it.get("question") or "").strip()
        sup = text_support(question, tr, it.get("anchor_segment", ""))
        if sup < 0.7:
            reasons.append(f"问题原话匹配度低({sup:.0%})")
        ans = normalize_answer(str(it.get("answer") or ""))
        if not question or not ans:
            reasons.append("问题或答案为空")
        rows.append({"time": round(t, 3), "question": question, "answer": ans, "review_reason": "；".join(reasons),
                     "evidence": str(it.get("answer_evidence") or "")})
    rows.sort(key=lambda r: r["time"])
    dedup: list[dict] = []
    for r in rows:
        if dedup and abs(r["time"] - dedup[-1]["time"]) < 2.0:
            dedup[-1]["review_reason"] = "；".join(x for x in [dedup[-1]["review_reason"], "与相邻题时间过近，可能重复"] if x)
            continue
        dedup.append(r)
    return dedup


DRAFT_SYSTEM = """你是小学数学课堂互动题整理助手。下面是老师讲课逐字稿的一个片段（ASR 可能有错字），
标注了【打点时刻】，即学生应开始作答的位置。请根据打点时刻之前老师提出的问题、之后老师公布或讲解的答案，
整理出一道客观题。

要求：
- question：尽量保留老师原话，可删口头填充词、修正明显 ASR 错字、合并“问题+互动指令”；不得编造老师没说过的内容。
- answer：老师明确说出选项字母时只写字母（如“C”）；否则写最小充分答案（数字、算式、词、单位、能/不能等）。
- 如果片段中没有可作答的客观问题，或答案无法确定，question/answer 尽量给出最接近的内容，并将 confident 设为 false、在 note 中说明。
只输出 JSON：{"question":"","answer":"","confident":true,"note":""}"""


def draft_mark(tr: dict, t: float, before: float = 10, after: float = 10) -> dict:
    segs = [s for s in tr["segments"] if s["end"] >= t - before and s["start"] <= t + after]
    if not segs:
        raise PipelineError("该时间点附近没有逐字稿内容")
    lines, marked = [], False
    for s in segs:
        if not marked and s["start"] >= t:
            lines.append("【打点时刻】")
            marked = True
        lines.append(f"{fmt_ts(s['start'])} {s['text']}")
        if not marked and s["start"] < t <= s["end"]:
            lines.append("【打点时刻】")
            marked = True
    if not marked:
        lines.append("【打点时刻】")
    r = chat_json(DRAFT_SYSTEM, "\n".join(lines), timeout=60)
    return {"question": str(r.get("question") or "").strip(), "answer": normalize_answer(str(r.get("answer") or "")),
            "confident": bool(r.get("confident", True)), "note": str(r.get("note") or "").strip(),
            "context": "\n".join(lines)}
