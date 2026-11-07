// 抽题规则：v2 为 JSON 结构化规则，v1 为旧版纯文本。两者都以字符串在前后端之间传递。

export const LAYERS = [
  { key: 'b1', title: '什么算互动题', group: 'B', hint: '收录条件与排除情况' },
  { key: 'b2', title: '问题怎么写', group: 'B', hint: '原话保留与可改动范围' },
  { key: 'b3', title: '答案怎么写', group: 'B', hint: '“选项字母优先”为固定规则，此处为通用补充' },
  { key: 'd1', title: '同一题多种问法', group: 'D', hint: '多次提问时选哪个版本' },
  { key: 'd2', title: '作答时间点', group: 'D', hint: '首要规则由“时间点偏好”决定，此处为补充' },
  { key: 'role', title: '角色说明', group: 'D', hint: '规则开头的角色设定' },
];

export const ANCHOR_OPTIONS = [
  { value: 'instruction', label: '互动指令结束后', desc: '如“你来选一选吧”的“吧”字说完' },
  { value: 'question', label: '问题本身结束后', desc: '如“进率是多少呢”的“呢”字说完' },
];

export function isStructured(text) {
  return (text || '').trimStart().startsWith('{');
}

export function parseRules(text) {
  if (!isStructured(text)) return null;
  try {
    const d = JSON.parse(text);
    return d && d.schema === 2 ? d : null;
  } catch {
    return null;
  }
}

export function hasRaw(cfg) {
  return !!(cfg?.raw || '').trim();
}

export function stringify(cfg) {
  if (hasRaw(cfg)) return JSON.stringify(cfg);
  const { raw, ...rest } = cfg;
  return JSON.stringify(rest);
}

export function clone(cfg) {
  return JSON.parse(JSON.stringify(cfg));
}

function sameExample(a, b) {
  return ['kind', 'title', 'original', 'correct', 'wrong', 'note'].every((k) => (a?.[k] || '') === (b?.[k] || ''));
}

// 列出相对默认规则改动过的部分（用于摘要与标记）
export function diffRules(text, defaultText) {
  const a = parseRules(text);
  const b = parseRules(defaultText);
  if (!a || !b) return (text || '').trim() === (defaultText || '').trim() ? [] : ['规则全文'];
  const out = [];
  const sa = a.settings, sb = b.settings;
  if (sa.density !== sb.density) out.push(`题量 ${sa.density} 题/分钟`);
  if (sa.judgment !== sb.judgment) out.push(sa.judgment ? '收录判断题' : '不收录判断题');
  if ((a.raw || '').trim() !== (b.raw || '').trim()) out.push('整段 Prompt');
  if (sa.anchor !== sb.anchor) out.push(`时间点：${ANCHOR_OPTIONS.find((o) => o.value === sa.anchor)?.label}`);
  for (const l of LAYERS) if ((a[l.key] || '').trim() !== (b[l.key] || '').trim()) out.push(l.title);
  const ea = a.examples || [], eb = b.examples || [];
  if (ea.length !== eb.length || ea.some((e, i) => !sameExample(e, eb[i]))) out.push(`示例（${ea.length} 条）`);
  return out;
}

export function summarize(text) {
  const c = parseRules(text);
  if (!c) return '旧版纯文本规则';
  const s = c.settings;
  return `${hasRaw(c) ? '整段自定义 Prompt · ' : ''}每分钟约 ${s.density} 题 · ${s.judgment ? '收录' : '不收录'}判断题 · 时间点在${ANCHOR_OPTIONS.find((o) => o.value === s.anchor)?.label} · ${c.examples.length} 条示例`;
}

export function emptyExample() {
  return { kind: 'positive', title: '', original: '', correct: '', wrong: '', note: '' };
}
