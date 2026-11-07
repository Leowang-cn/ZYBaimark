export class ApiError extends Error {
  constructor(status, message, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
  });
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    if (res.status === 401 && path !== '/api/auth/login') onUnauthorized();
    const detail = data?.detail;
    const msg = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((d) => d.msg).join('；') : `请求失败（${res.status}）`;
    throw new ApiError(res.status, msg, data);
  }
  return data;
}

export function fmtTs(t) {
  const ms = Math.round(Math.max(t || 0, 0) * 1000);
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const r = ms % 1000;
  const p = (n, w = 2) => String(n).padStart(w, '0');
  return `${p(h)}:${p(m)}:${p(s)}.${p(r, 3)}`;
}

export function parseTs(text) {
  const m = /^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\s*$/.exec(text || '');
  if (!m) return null;
  const [, h, mi, s, ms] = m;
  return (+h || 0) * 3600 + +mi * 60 + +s + +(ms || '0').padEnd(3, '0') / 1000;
}

export const STAGE_LABEL = {
  probe: '读取视频', audio: '抽取音频', silence: '检测停顿', asr: '语音转写',
  llm1: 'AI 抽题（1/3）', llm2: 'AI 回扫（2/3）', llm3: 'AI 复核（3/3）', finalize: '整理结果',
};
export const STATUS_LABEL = {
  queued: '排队中', running: '处理中', done: '已完成', failed: '失败', resolve_failed: '解析失败',
};
