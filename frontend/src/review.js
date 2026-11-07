// 审核页的纯计算：智能起播点、打点置信度。

const norm = (t) => (t || '').replace(/[\s，。！？、,.!?：:；;“”"'‘’（）()…—\-~～]/g, '');

function bigrams(s) {
  const out = new Set();
  for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2));
  return out;
}

// 分段与问题开头的相似度（0–1）：问题开头的二元组在分段中出现的比例
function headScore(head, segText) {
  const hb = bigrams(head);
  const sb = bigrams(norm(segText));
  if (hb.size < 2 || sb.size < 2) return 0;
  let hit = 0;
  for (const b of hb) if (sb.has(b)) hit++;
  return hit / Math.min(hb.size, Math.max(sb.size, 3));
}

export const PLAY_MODES = [
  { value: 'question', label: '从问题开头' },
  { value: 'context', label: '问题开头再往前一句' },
  { value: 'fixed', label: '固定提前 N 秒' },
];

const MAX_BACK = 40;
const PAD = 0.3;

/**
 * 计算某个打点的起播时刻。
 * 在打点前 MAX_BACK 秒内找与问题开头最相似的分段（多次提问时取最早一次），从该分段开头稍前播放；
 * 不越过上一个打点；匹配不到时退回固定提前量。
 * 返回 { time, how, segId }，how 为 question | context | fixed | fallback。
 */
export function playStart(mark, segments, opts = {}) {
  const { mode = 'question', preroll = 1, prevTime = null } = opts;
  const t = mark.time_s;
  const floor = Math.max(0, t - MAX_BACK, prevTime != null && prevTime < t ? prevTime + 0.2 : 0);
  const fixed = { time: Math.max(0, t - preroll), how: 'fixed', segId: null };
  if (mode === 'fixed') return fixed;
  const q = norm(mark.question);
  const cands = (segments || []).filter((s) => s.start >= floor - 1 && s.start < t);
  if (q.length < 3 || !cands.length) return { ...fixed, how: 'fallback' };
  const head = q.slice(0, 12);
  const scored = cands.map((s) => ({ s, v: headScore(head, s.text) }));
  const best = Math.max(...scored.map((x) => x.v));
  if (best < 0.5) return { ...fixed, how: 'fallback' };
  const pick = scored.find((x) => x.v >= best * 0.85 && x.v >= 0.5).s;
  let seg = pick;
  if (mode === 'context') {
    const i = segments.indexOf(pick);
    if (i > 0 && segments[i - 1].start >= floor) seg = segments[i - 1];
  }
  return { time: Math.max(floor, seg.start - PAD), how: seg === pick ? 'question' : 'context', segId: seg.id };
}

// 由程序校验原因推断置信度：high 无疑点；mid 仅轻微定位/措辞问题；low 有实质疑点
const MINOR = [/锚点文字未精确匹配/, /无词级时间戳/, /问题原话匹配度低\((?:[5-6]\d)%\)/];

export function splitReasons(reason) {
  return (reason || '').split('；').map((x) => x.trim()).filter(Boolean);
}

export function confidence(mark) {
  if (mark.source === 'manual') return 'manual';
  const rs = splitReasons(mark.review_reason);
  if (!rs.length) return 'high';
  return rs.every((r) => MINOR.some((p) => p.test(r))) ? 'mid' : 'low';
}

export const CONF_LABEL = { high: '置信度高', mid: '置信度中', low: '置信度低', manual: '人工添加' };
