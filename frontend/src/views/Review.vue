<script setup>
import { computed, inject, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { api, fmtTs, parseTs, STAGE_LABEL } from '../api';
import RulesDrawer from '../components/RulesDrawer.vue';

const props = defineProps({ id: String });
const toast = inject('toast');

const video = ref(null);
const gen = ref(null);
const segments = ref([]);
const selectedId = ref(null);
const currentTime = ref(0);
const playing = ref(false);
const rate = ref(1);
const preroll = ref(Number(localStorage.getItem('preroll') ?? 1));
const showDeleted = ref(false);
const defaultPrompt = ref('');
const regenOpen = ref(false);
const regenPrompt = ref('');
const compareWith = ref(null);
const timeDraft = ref({});
const player = ref(null);
const listEl = ref(null);
let hls = null;
let pollTimer = null;

watch(preroll, (v) => localStorage.setItem('preroll', String(Math.max(0, Number(v) || 0))));

const marks = computed(() => (gen.value?.marks || []).filter((m) => showDeleted.value || !m.deleted));
const liveMarks = computed(() => (gen.value?.marks || []).filter((m) => !m.deleted));
const selected = computed(() => gen.value?.marks.find((m) => m.id === selectedId.value) || null);
const duration = computed(() => video.value?.duration_s || 0);
const isCurrent = computed(() => gen.value && video.value && gen.value.id === video.value.current_generation_id);
const stats = computed(() => ({
  total: liveMarks.value.length,
  confirmed: liveMarks.value.filter((m) => m.confirmed).length,
}));
const nearbySegments = computed(() => {
  const t = currentTime.value;
  return segments.value.filter((s) => s.end >= t - 20 && s.start <= t + 20);
});
const diffInfo = computed(() => {
  if (!compareWith.value || !gen.value) return null;
  const key = (m) => `${m.question}|${m.answer}`;
  const other = compareWith.value.marks.filter((m) => !m.deleted);
  const mine = liveMarks.value;
  const near = (a, list) => list.find((b) => Math.abs(a.time_s - b.time_s) < 3);
  return {
    added: mine.filter((m) => !near(m, other)).length,
    removed: other.filter((m) => !near(m, mine)),
    changed: mine.filter((m) => { const o = near(m, other); return o && key(o) !== key(m); }).length,
  };
});

async function loadVideo() {
  video.value = await api(`/api/videos/${props.id}`);
}

async function loadGen(gid) {
  gen.value = await api(`/api/generations/${gid}`);
  timeDraft.value = {};
  compareWith.value = null;
}

async function init() {
  await loadVideo();
  const gid = video.value.current_generation_id || video.value.generations[0]?.id;
  if (gid) await loadGen(gid);
  api(`/api/videos/${props.id}/transcript`).then((r) => (segments.value = r.segments)).catch(() => {});
  api('/api/prompts/default').then((p) => (defaultPrompt.value = p.content)).catch(() => {});
  await nextTick();
  setupPlayer();
}

function setupPlayer() {
  const el = player.value;
  if (!el || !video.value) return;
  el.src = video.value.url;
  el.onerror = async () => {
    if (!video.value.encode_url || hls) return;
    const { default: Hls } = await import('hls.js');
    if (!Hls.isSupported()) return;
    toast('原视频加载失败，切换为 240p 转码版本');
    hls = new Hls();
    hls.loadSource(video.value.encode_url);
    hls.attachMedia(el);
  };
}

function onTime() {
  currentTime.value = player.value?.currentTime || 0;
}

function seek(t, play = true) {
  const el = player.value;
  if (!el) return;
  el.currentTime = Math.max(0, Math.min(t, duration.value || t));
  if (play) el.play().catch(() => {});
}

function nudge(d) {
  seek((player.value?.currentTime || 0) + d, false);
}

function togglePlay() {
  const el = player.value;
  if (!el) return;
  el.paused ? el.play().catch(() => {}) : el.pause();
}

function selectMark(m, play = true) {
  selectedId.value = m.id;
  if (play) seek(m.time_s - (Number(preroll.value) || 0));
}

function scrollToSelected() {
  nextTick(() => listEl.value?.querySelector(`[data-id="${selectedId.value}"]`)?.scrollIntoView({ block: 'nearest' }));
}

function replaceMark(m) {
  const i = gen.value.marks.findIndex((x) => x.id === m.id);
  if (i >= 0) gen.value.marks[i] = m;
  gen.value.marks.sort((a, b) => a.time_s - b.time_s || a.id - b.id);
}

async function patch(m, fields) {
  try {
    const r = await api(`/api/marks/${m.id}`, { method: 'PATCH', body: { revision: m.revision, ...fields } });
    replaceMark(r);
    return r;
  } catch (e) {
    if (e.status === 409 && e.data?.mark) replaceMark(e.data.mark);
    toast(e.message, true);
  }
}

function saveText(m, field, value) {
  if ((m[field] || '') === value.trim()) return;
  patch(m, { [field]: value });
}

function saveTime(m) {
  const raw = timeDraft.value[m.id];
  if (raw === undefined) return;
  const t = parseTs(raw);
  delete timeDraft.value[m.id];
  if (t === null) return toast('时间格式应为 HH:MM:SS.mmm', true);
  if (Math.abs(t - m.time_s) > 0.0005) patch(m, { time_s: t });
}

async function setTimeToCurrent(m = selected.value) {
  if (!m) return toast('请先选中一行', true);
  const r = await patch(m, { time_s: Math.round(currentTime.value * 1000) / 1000 });
  if (r) toast(`已将时间设为 ${r.time}`);
}

const drafting = ref({});
const draftWindow = ref(Number(localStorage.getItem('draftWindow') ?? 10));
watch(draftWindow, (v) => localStorage.setItem('draftWindow', String(Math.min(60, Math.max(3, Number(v) || 10)))));

async function addAtCurrent() {
  const t = Math.round(currentTime.value * 1000) / 1000;
  let m;
  try {
    m = await api(`/api/generations/${gen.value.id}/marks`, {
      method: 'POST', body: { time_s: t, question: '', answer: '' },
    });
  } catch (e) {
    return toast(e.message, true);
  }
  gen.value.marks.push(m);
  gen.value.marks.sort((a, b) => a.time_s - b.time_s || a.id - b.id);
  selectedId.value = m.id;
  scrollToSelected();
  if (segments.value.length) draftFor(m);
}

async function draftFor(m) {
  const w = Number(draftWindow.value) || 10;
  drafting.value[m.id] = true;
  try {
    const d = await api(`/api/videos/${props.id}/draft-mark`, {
      method: 'POST', body: { time_s: m.time_s, before_s: w, after_s: w },
    });
    const cur = gen.value.marks.find((x) => x.id === m.id);
    // 用户在生成期间已自行填写的字段不覆盖
    const fields = {};
    if (cur && !cur.question && d.question) fields.question = d.question;
    if (cur && !cur.answer && d.answer) fields.answer = d.answer;
    if (cur && Object.keys(fields).length) await patch(cur, fields);
    if (!d.confident) toast(`AI 草拟结果不确定：${d.note || '请人工核对'}`, true);
  } catch (e) {
    toast(`自动生成问题失败：${e.message}，请手动填写`, true);
  } finally {
    delete drafting.value[m.id];
  }
}

async function removeMark(m) {
  const r = await patch(m, { deleted: true });
  if (r) toast('已删除，可在“显示已删除”中恢复');
}

async function switchGen(gid) {
  await loadGen(Number(gid));
}

async function setCurrent() {
  try {
    await api(`/api/videos/${props.id}/current-generation`, { method: 'POST', body: { generation_id: gen.value.id } });
    await loadVideo();
    toast(`已将 v${gen.value.version_no} 设为当前版本`);
  } catch (e) {
    toast(e.message, true);
  }
}

async function toggleCompare() {
  if (compareWith.value) return (compareWith.value = null);
  const prev = video.value.generations.find((g) => g.version_no < gen.value.version_no);
  if (!prev) return toast('没有更早的版本', true);
  compareWith.value = await api(`/api/generations/${prev.id}`);
}

function openRegen() {
  regenPrompt.value = gen.value?.prompt_text || defaultPrompt.value;
  regenOpen.value = true;
}

async function regen() {
  try {
    await api(`/api/videos/${props.id}/generations`, { method: 'POST', body: { prompt_text: regenPrompt.value } });
    regenOpen.value = false;
    toast('已提交重新生成，约 20 秒～1 分钟');
    await loadVideo();
    startPolling();
  } catch (e) {
    toast(e.message, true);
  }
}

function startPolling() {
  clearInterval(pollTimer);
  const before = video.value.generations.length;
  pollTimer = setInterval(async () => {
    await loadVideo();
    if (!['queued', 'running'].includes(video.value.status)) {
      clearInterval(pollTimer);
      if (video.value.status === 'failed') return toast(`重新生成失败：${video.value.error}`, true);
      if (video.value.generations.length > before) {
        const latest = video.value.generations[0];
        if (latest.id === video.value.current_generation_id) {
          await loadGen(latest.id);
          toast(`已生成 v${latest.version_no} 并设为当前版本`);
        } else {
          toast(`已生成 v${latest.version_no}。当前版本有人工修改，未自动切换，可在版本下拉中查看`);
        }
      }
    }
  }, 2500);
}

function exportFile(format) {
  const unconfirmed = liveMarks.value.filter((m) => !m.confirmed).length;
  if (!liveMarks.value.length) return toast('没有可导出的打点', true);
  if (unconfirmed && !confirm(`还有 ${unconfirmed} 条打点未确认，仍然导出全部 ${liveMarks.value.length} 条吗？`)) return;
  window.location.href = `/api/export?generation_id=${gen.value.id}&format=${format}`;
}

function moveSelection(d) {
  const list = marks.value;
  if (!list.length) return;
  const i = list.findIndex((m) => m.id === selectedId.value);
  const next = list[Math.max(0, Math.min(list.length - 1, i < 0 ? 0 : i + d))];
  selectMark(next);
  scrollToSelected();
}

function onKey(e) {
  const tag = e.target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || regenOpen.value) return;
  const k = e.key;
  if (k === ' ') { e.preventDefault(); togglePlay(); }
  else if (k === 'ArrowLeft') { e.preventDefault(); nudge(e.shiftKey ? -0.1 : -1); }
  else if (k === 'ArrowRight') { e.preventDefault(); nudge(e.shiftKey ? 0.1 : 1); }
  else if (k === 'ArrowUp') { e.preventDefault(); moveSelection(-1); }
  else if (k === 'ArrowDown') { e.preventDefault(); moveSelection(1); }
  else if (k === 'Enter') { e.preventDefault(); setTimeToCurrent(); }
  else if (k === 'n' || k === 'N') { e.preventDefault(); addAtCurrent(); }
  else if ((k === 'c' || k === 'C') && selected.value) { e.preventDefault(); patch(selected.value, { confirmed: !selected.value.confirmed }); }
}

onMounted(() => {
  init().catch((e) => toast(e.message, true));
  window.addEventListener('keydown', onKey);
});
onUnmounted(() => {
  window.removeEventListener('keydown', onKey);
  clearInterval(pollTimer);
  hls?.destroy();
});

function markLeft(t) {
  return duration.value ? `${(t / duration.value) * 100}%` : '0';
}
function onTrackClick(e) {
  const rect = e.currentTarget.getBoundingClientRect();
  seek(((e.clientX - rect.left) / rect.width) * duration.value, false);
}
</script>

<template>
  <div class="page wide review" v-if="video">
    <div class="row wrap head">
      <router-link :to="`/tasks/${video.task_id}`" class="small">← {{ video.task_name }}</router-link>
      <b class="title" :title="video.name">{{ video.material_id }} {{ video.name }}</b>
      <span class="spacer" />
      <template v-if="video.generations.length">
        <select :value="gen?.id" @change="switchGen($event.target.value)">
          <option v-for="g in video.generations" :key="g.id" :value="g.id">
            v{{ g.version_no }} · {{ g.mark_count }} 题 · {{ new Date(g.created_at + 'Z').toLocaleString() }}{{ g.id === video.current_generation_id ? ' · 当前' : '' }}
          </option>
        </select>
        <button v-if="gen && !isCurrent" @click="setCurrent">设为当前版本</button>
        <button v-if="video.generations.length > 1" @click="toggleCompare">{{ compareWith ? '取消对比' : '与上一版对比' }}</button>
      </template>
      <button :disabled="['queued', 'running'].includes(video.status)" @click="openRegen">
        {{ ['queued', 'running'].includes(video.status) ? `生成中：${STAGE_LABEL[video.stage] || '排队'}` : '修改规则并重新生成' }}
      </button>
      <button @click="exportFile('csv')" :disabled="!gen">导出 CSV</button>
      <button class="primary" @click="exportFile('xlsx')" :disabled="!gen">导出 Excel</button>
    </div>

    <div v-if="compareWith && diffInfo" class="panel compare small">
      与 v{{ compareWith.version_no }} 相比：新增 {{ diffInfo.added }} 题，内容变化 {{ diffInfo.changed }} 题，减少 {{ diffInfo.removed.length }} 题
      <span v-if="diffInfo.removed.length" class="muted">（{{ diffInfo.removed.map((m) => `${m.time} ${m.question.slice(0, 12)}`).join('；') }}）</span>
      <span class="muted">（时间相差 3 秒内视为同一题）</span>
    </div>

    <div class="layout">
      <section class="left">
        <div class="panel player-panel">
          <video ref="player" controls preload="metadata" playsinline @timeupdate="onTime" @seeked="onTime"
            @play="playing = true" @pause="playing = false" @ratechange="rate = player.playbackRate" />
          <div class="track-legend small muted">
            <span><i class="lg-line" /> 竖线 = 作答时间点（老师说完问题、学生开始作答）</span>
            <span><i class="lg-dot" style="background:#f59e0b" /> 待确认</span>
            <span><i class="lg-dot" style="background:var(--ok)" /> 已确认</span>
            <span><i class="lg-cur" /> 当前播放位置</span>
          </div>
          <div class="track" @click="onTrackClick" title="点击跳转">
            <div class="cursor" :style="{ left: markLeft(currentTime) }" />
            <div v-for="m in liveMarks" :key="m.id" class="pin" :class="{ sel: m.id === selectedId, ok: m.confirmed }"
              :style="{ left: markLeft(m.time_s) }" :title="`${m.time} 学生从这里开始作答\n${m.question}`"
              @click.stop="selectMark(m); scrollToSelected()"><i class="flag" /><i class="line" /></div>
          </div>
          <div class="row wrap controls">
            <span class="mono now">{{ fmtTs(currentTime) }}</span>
            <button class="sm" @click="nudge(-1)">-1s</button>
            <button class="sm" @click="nudge(-0.1)">-0.1s</button>
            <button class="sm" @click="togglePlay">{{ playing ? '暂停' : '播放' }}</button>
            <button class="sm" @click="nudge(0.1)">+0.1s</button>
            <button class="sm" @click="nudge(1)">+1s</button>
            <select v-model.number="rate" class="sm-select" @change="player.playbackRate = rate">
              <option :value="0.5">0.5x</option><option :value="0.75">0.75x</option><option :value="1">1x</option>
              <option :value="1.25">1.25x</option><option :value="1.5">1.5x</option><option :value="2">2x</option>
            </select>
            <label class="small">提前 <input v-model.number="preroll" type="number" min="0" max="30" step="0.5" style="width: 56px" /> 秒播放</label>
            <span class="spacer" />
            <button class="sm" :disabled="!selected" @click="setTimeToCurrent()">当前时间写入选中行</button>
            <button class="sm primary" :disabled="!gen" :title="`在当前时间新增，并用前后 ${draftWindow} 秒逐字稿让 AI 草拟问题和答案`" @click="addAtCurrent">以当前时间新增</button>
            <label class="small muted" title="AI 草拟时参考打点前、后各多少秒的逐字稿">参考前后 <input v-model.number="draftWindow" type="number" min="3" max="60" step="1" style="width: 48px" /> 秒</label>
          </div>
          <p class="muted small keys">快捷键：空格 播放/暂停 · ←/→ ±1s · Shift+←/→ ±0.1s · ↑/↓ 上/下一题 · Enter 当前时间写入选中行 · N 新增 · C 确认/取消确认</p>
        </div>

        <div class="panel transcript">
          <h2>逐字稿（当前时间前后 20 秒）</h2>
          <div v-if="!segments.length" class="muted small">逐字稿加载中或尚未生成</div>
          <p v-for="s in nearbySegments" :key="s.id" class="seg" :class="{ active: currentTime >= s.start && currentTime < s.end }"
            @click="seek(s.start, false)">
            <span class="mono muted">{{ fmtTs(s.start).slice(3) }}</span> {{ s.text }}
          </p>
        </div>
      </section>

      <section class="right panel">
        <div class="row list-head">
          <b>打点 {{ stats.total }}</b>
          <span class="muted small">已确认 {{ stats.confirmed }} / {{ stats.total }}</span>
          <span v-if="gen" class="muted small">· 目标约 {{ gen.target_count }}</span>
          <span class="spacer" />
          <label class="small"><input v-model="showDeleted" type="checkbox" /> 显示已删除</label>
        </div>
        <div v-if="video.status === 'running' && !gen" class="muted">正在处理：{{ STAGE_LABEL[video.stage] || video.stage }}</div>
        <div ref="listEl" class="marks">
          <div v-for="m in marks" :key="m.id" :data-id="m.id" class="mark"
            :class="{ sel: m.id === selectedId, ok: m.confirmed, del: m.deleted }" @click="selectMark(m, false)">
            <div class="row">
              <button class="sm play" title="从该时间点（减去提前量）播放" @click.stop="selectMark(m)">▶</button>
              <input class="mono time" :value="timeDraft[m.id] ?? m.time" :disabled="m.deleted"
                @input="timeDraft[m.id] = $event.target.value" @blur="saveTime(m)" @keydown.enter="$event.target.blur()" />
              <span v-if="m.source === 'manual'" class="tag">人工</span>
              <span v-else-if="m.ai_time_s !== null && Math.abs(m.ai_time_s - m.time_s) > 0.0005" class="tag" :title="`AI 原时间 ${fmtTs(m.ai_time_s)}`">已调时间</span>
              <span class="spacer" />
              <template v-if="!m.deleted">
                <button class="sm" :class="{ primary: m.confirmed }" @click.stop="patch(m, { confirmed: !m.confirmed })">
                  {{ m.confirmed ? '已确认' : '确认' }}
                </button>
                <button class="sm danger" @click.stop="removeMark(m)">删除</button>
              </template>
              <button v-else class="sm" @click.stop="patch(m, { deleted: false })">恢复</button>
            </div>
            <div v-if="drafting[m.id]" class="small drafting">AI 正在根据前后 {{ draftWindow }} 秒逐字稿生成问题和答案…</div>
            <textarea class="q" rows="2" :value="m.question" :placeholder="drafting[m.id] ? '生成中…' : '问题'" :disabled="m.deleted"
              @blur="saveText(m, 'question', $event.target.value)" />
            <div class="row">
              <span class="small muted">答案</span>
              <input class="a" :value="m.answer" placeholder="答案" :disabled="m.deleted"
                @blur="saveText(m, 'answer', $event.target.value)" @keydown.enter="$event.target.blur()" />
            </div>
            <div v-if="m.review_reason" class="small warn-text">⚠ {{ m.review_reason }}</div>
            <div v-if="m.evidence && m.id === selectedId" class="small muted">答案依据：{{ m.evidence }}</div>
          </div>
          <div v-if="gen && !marks.length" class="muted">没有打点，可用“以当前时间新增”手动添加</div>
        </div>
      </section>
    </div>

    <RulesDrawer v-model:open="regenOpen" v-model="regenPrompt" :default-text="defaultPrompt" allow-save-default
      @saved-default="(t) => (defaultPrompt = t)"
      title="修改规则并重新生成" hint="复用已有音频与转写，只重新调用 AI，生成新版本；当前版本的人工修改不会被覆盖。" confirm-text="开始重新生成" @confirm="regen" />
  </div>
</template>

<style scoped>
.review { padding: 12px 16px; }
.head { margin-bottom: 12px; }
.head .title { max-width: 520px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.compare { margin-bottom: 12px; padding: 8px 12px; background: var(--primary-weak); }
.layout { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(380px, 1fr); gap: 12px; height: calc(100vh - 52px - 70px); }
.left { display: flex; flex-direction: column; gap: 12px; min-height: 0; }
.player-panel video { width: 100%; max-height: 52vh; background: #000; border-radius: 6px; display: block; }
.track-legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; }
.track-legend i { display: inline-block; vertical-align: middle; margin-right: 3px; }
.lg-line { width: 2px; height: 12px; background: #b45309; }
.lg-dot { width: 8px; height: 8px; border-radius: 2px; }
.lg-cur { width: 2px; height: 12px; background: var(--primary); }
.track { position: relative; height: 30px; margin: 6px 0 6px; background: #eef0f3; border-radius: 4px; cursor: pointer; }
.track .cursor { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: var(--primary); z-index: 3; pointer-events: none; }
.track .pin { position: absolute; top: 0; bottom: 0; width: 12px; margin-left: -6px; cursor: pointer; --c: #f59e0b; }
.track .pin.ok { --c: var(--ok); }
.track .pin .line { position: absolute; left: 5px; top: 0; bottom: 0; width: 2px; background: var(--c); }
.track .pin .flag { position: absolute; left: 6px; top: 0; width: 8px; height: 7px; background: var(--c); clip-path: polygon(0 0, 100% 50%, 0 100%); }
.track .pin:hover .line, .track .pin.sel .line { width: 3px; left: 4.5px; }
.track .pin.sel { z-index: 2; }
.track .pin.sel .line { box-shadow: 0 0 0 2px var(--primary-weak); }
.controls { gap: 6px; }
.now { font-size: 15px; min-width: 104px; }
.sm-select { height: 26px; padding: 0 4px; font-size: 12px; }
.keys { margin: 8px 0 0; }
.transcript { flex: 1; overflow: auto; min-height: 120px; }
.seg { margin: 4px 0; cursor: pointer; padding: 2px 4px; border-radius: 4px; line-height: 1.5; }
.seg:hover { background: #f3f4f6; }
.seg.active { background: var(--primary-weak); }
.right { display: flex; flex-direction: column; min-height: 0; padding: 12px; }
.list-head { margin-bottom: 8px; }
.marks { overflow: auto; flex: 1; display: flex; flex-direction: column; gap: 8px; padding-right: 4px; }
.mark { border: 1px solid var(--border); border-radius: 8px; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
.mark.sel { border-color: var(--primary); box-shadow: 0 0 0 2px var(--primary-weak); }
.mark.ok { background: #f6fdf8; }
.mark.del { opacity: .5; }
.mark .time { width: 118px; height: 26px; }
.mark .play { width: 28px; justify-content: center; }
.mark .q { font-size: 13px; }
.mark .a { flex: 1; height: 28px; }
.warn-text { color: var(--warn); }
.drafting { color: var(--primary); }
</style>
