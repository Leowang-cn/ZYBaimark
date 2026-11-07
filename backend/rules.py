"""抽题规则：结构化（v2，JSON）与旧版纯文本（v1）两种格式的解析、校验与渲染。

结构化规则以 JSON 字符串存储在原有的 prompt_text 字段中，旧数据无需迁移。
可选字段 raw 为用户整段粘贴的 Prompt：非空时替代分层规则与示例，基本设置仍生效。
"""
from __future__ import annotations

import json
import math

from .config import ROOT

SCHEMA = 2
BUILTIN_FILE = ROOT / "prompts" / "default_v2.json"
TEXT_FIELDS = ("role", "b1", "b2", "b3", "d1", "d2")
EXAMPLE_FIELDS = ("title", "original", "correct", "wrong", "note")
JUDGMENT_ANSWERS = {"是", "否", "对", "错", "能", "不能", "对的", "错的", "正确", "错误", "√", "×"}

ANCHOR_TEXT = {
    "instruction": "问题后有“选一选、算一算、填一填、想一想”等互动指令，且指令后有真实作答停顿时，锚定在指令最后一个字说完处。",
    "question": "锚定在问题本身最后一个字说完处，即使后面还有互动指令。",
}
LETTER_FIRST = ("老师明确说出选项字母时，答案只写字母（如“C”），不写选项内容；老师只说了序号（如“第2个”）就保留序号；"
                "都没说才写具体内容。不得根据画面或选项顺序自行推断字母，禁止写成“C选项”“C（1000）”等形式。")

SYSTEM_DESCRIPTION = """以下由程序保证，不需要也不应在规则中修改：
1. 扫描流程：第 1 轮候选发现 → 程序列出尚未判断的互动指令与长停顿 → 第 2 轮逐个判断（题量不足时完整回扫）→ 第 3 轮逐题复核。
2. 输入格式：带分段编号和时间的逐字稿；“<停顿 Xs>”为程序检测到的静音。
3. 输出格式：JSON；时间点只输出“锚点分段 + 锚点处最后几个字”，由程序换算为 HH:MM:SS.mmm 并对齐到附近的停顿。
4. 复核要求：第 3 轮按业务规则、专家细则和示例逐题检查，并做反向核验（仅凭问题和答案能否定位回原话），不符合的修改或删除。
5. 程序校验：原话匹配度、时间是否越界、锚点后是否有停顿、相邻题是否过近、答案格式规范化（“C选项”→“C”）、空值；
   基本设置为“不收录判断题”时，答案为是/否、对/错等的题目会被标记待复核。未通过的标记“待人工复核”。"""


class RulesError(ValueError):
    pass


def is_structured(text: str) -> bool:
    return (text or "").lstrip().startswith("{")


def load(text: str) -> dict | str:
    """结构化规则返回校验后的 dict，旧版纯文本原样返回。"""
    if not is_structured(text):
        return text
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RulesError("规则格式错误（不是合法 JSON）") from e
    return validate(data)


def validate(data) -> dict:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RulesError("规则格式错误（schema 不是 2）")
    s = data.get("settings") or {}
    try:
        density = float(s.get("density", 1.0))
    except (TypeError, ValueError) as e:
        raise RulesError("题量密度必须是数字") from e
    if not 0.2 <= density <= 3:
        raise RulesError("题量密度必须在 0.2–3 题/分钟之间")
    anchor = s.get("anchor", "instruction")
    if anchor not in ANCHOR_TEXT:
        raise RulesError("时间点偏好只能是 instruction 或 question")
    out = {"schema": SCHEMA,
           "settings": {"density": round(density, 2), "judgment": bool(s.get("judgment", True)), "anchor": anchor}}
    for k in TEXT_FIELDS:
        v = data.get(k, "")
        if not isinstance(v, str):
            raise RulesError(f"{k} 必须是文本")
        out[k] = v
    raw = data.get("raw") or ""
    if not isinstance(raw, str):
        raise RulesError("整段 Prompt 必须是文本")
    if raw.strip():
        out["raw"] = raw
    elif not out["b1"].strip():
        raise RulesError("“什么算互动题”不能为空")
    examples = data.get("examples") or []
    if not isinstance(examples, list) or len(examples) > 50:
        raise RulesError("示例最多 50 条")
    out["examples"] = []
    for ex in examples:
        if not isinstance(ex, dict):
            raise RulesError("示例格式错误")
        item = {"kind": "negative" if ex.get("kind") == "negative" else "positive"}
        for k in EXAMPLE_FIELDS:
            item[k] = str(ex.get(k) or "")
        if item["original"].strip() or item["correct"].strip():
            out["examples"].append(item)
    return out


def check_text(text: str) -> None:
    load(text)


def density(cfg: dict | str) -> float:
    return cfg["settings"]["density"] if isinstance(cfg, dict) else 1.0


def allow_judgment(cfg: dict | str) -> bool:
    return cfg["settings"]["judgment"] if isinstance(cfg, dict) else True


def target_count(cfg: dict | str, duration: float) -> int:
    return max(1, math.ceil(duration * density(cfg) / 60))


def render(cfg: dict | str, duration: float | None = None, bare: bool = False) -> str:
    """渲染为发送给模型的规则正文；旧版纯文本原样返回。bare=True 时忽略 raw 且不含基本设置，用作整段粘贴的起点。"""
    if isinstance(cfg, str):
        return cfg
    s = cfg["settings"]
    n = f"（本视频约 {target_count(cfg, duration)} 道）" if duration else ""
    basics = [f"- 目标题量约为每分钟 {s['density']:g} 道{n}，这是整段平均值，质量优先，不得为凑数收录不合格问题。",
              "- 判断题（是/否、对/错、能/不能）可以收录。" if s["judgment"] else "- 不收录判断题（是/否、对/错、能/不能）。"]
    if not bare and cfg.get("raw", "").strip():
        # 整段自定义 Prompt：替代分层规则与示例，基本设置与固定规则仍附加在后并优先生效
        return "\n".join([cfg["raw"].strip(), "", "【程序设置（与上文冲突时以此为准）】", *basics,
                          f"- 答案写法：{LETTER_FIRST}", f"- 作答时间点首要规则：{ANCHOR_TEXT[s['anchor']]}"]) + "\n"
    parts = [cfg["role"].strip(), "",
             *([] if bare else ["一、基本设置", *basics, ""]),
             "二、什么算互动题", cfg["b1"].strip(),
             "", "三、问题怎么写", cfg["b2"].strip(),
             "", "四、答案怎么写", LETTER_FIRST, cfg["b3"].strip(),
             "", "五、同一题多种问法", cfg["d1"].strip(),
             "", "六、作答时间点", f"首要规则：{ANCHOR_TEXT[s['anchor']]}", cfg["d2"].strip()]
    if cfg["examples"]:
        parts += ["", "七、示例"]
        for i, ex in enumerate(cfg["examples"], 1):
            head = f"示例{i}（{'反例' if ex['kind'] == 'negative' else '正例'}"
            head += f" · {ex['title'].strip()}）" if ex["title"].strip() else "）"
            parts.append(head)
            for label, k in [("老师原话", "original"), ("正确做法", "correct"), ("错误做法", "wrong"), ("说明", "note")]:
                if ex[k].strip():
                    parts.append(f"{label}：{ex[k].strip()}")
    return "\n".join(p for p in parts if p is not None).strip() + "\n"


def builtin_text() -> str:
    return BUILTIN_FILE.read_text(encoding="utf-8")


def builtin_cfg() -> dict:
    return validate(json.loads(builtin_text()))

