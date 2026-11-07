// 抽题规则按“一、二、三……”章节拆分为可单独编辑的字段；拼接后与原文逐字一致
const HEADING = /^[一二三四五六七八九十]+、.+$/;

export function splitPrompt(text) {
  const lines = (text || '').split(/(?<=\n)/);
  const chunks = [];
  let cur = { title: '角色与目标', head: '', body: '' };
  for (const line of lines) {
    const bare = line.replace(/\r?\n$/, '').trim();
    if (HEADING.test(bare)) {
      if (cur.head || cur.body) chunks.push(cur);
      cur = { title: bare.replace(/^[一二三四五六七八九十]+、/, ''), head: line, body: '' };
    } else {
      cur.body += line;
    }
  }
  if (cur.head || cur.body) chunks.push(cur);
  return chunks.map((c, i) => ({ ...c, key: `${i}-${c.title}` }));
}

export function joinPrompt(chunks) {
  return chunks.map((c) => c.head + c.body).join('');
}

export function changedTitles(text, defaultText) {
  const def = new Map(splitPrompt(defaultText).map((c) => [c.title, c.body.trim()]));
  return new Set(splitPrompt(text).filter((c) => def.get(c.title) !== c.body.trim()).map((c) => c.title));
}
