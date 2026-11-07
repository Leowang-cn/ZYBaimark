<script setup>
// 结构化抽题规则编辑器（抽屉）：A 基本设置 / B 业务规则 / C 示例库 / D 专家细则 / P 整段粘贴 / E 系统内置
import { computed, ref, watch } from 'vue';
import { api } from '../api';
import { ANCHOR_OPTIONS, LAYERS, clone, diffRules, emptyExample, hasRaw, parseRules, stringify } from '../rules';

const props = defineProps({
  open: Boolean,
  modelValue: { type: String, default: '' },
  defaultText: { type: String, default: '' },
  title: { type: String, default: '抽题规则' },
  hint: { type: String, default: '修改只对本次任务生效，不影响默认规则。' },
  confirmText: { type: String, default: '使用这份规则' },
  // 显示“同时保存为默认规则”选项
  allowSaveDefault: Boolean,
});
const emit = defineEmits(['update:open', 'update:modelValue', 'confirm', 'saved-default']);

const cfg = ref(null);
const legacy = ref('');
const tab = ref('A');
const preview = ref(null);
const previewOpen = ref(false);
const saveDefault = ref(false);
const busy = ref(false);
const err = ref('');

watch(() => props.open, (o) => {
  if (!o) return;
  const parsed = parseRules(props.modelValue);
  cfg.value = parsed ? clone(parsed) : null;
  legacy.value = parsed ? '' : props.modelValue;
  tab.value = hasRaw(cfg.value) ? 'P' : 'A';
  previewOpen.value = false;
  saveDefault.value = false;
  err.value = '';
});

const defaults = computed(() => parseRules(props.defaultText));
const draftText = computed(() => (cfg.value ? stringify(cfg.value) : legacy.value));
const changes = computed(() => diffRules(draftText.value, props.defaultText));
const tabs = computed(() => [
  { key: 'A', label: '基本设置', dirty: changes.value.some((c) => /题量|判断题|时间点/.test(c)) },
  { key: 'B', label: '业务规则', dirty: LAYERS.filter((l) => l.group === 'B').some((l) => changes.value.includes(l.title)) },
  { key: 'C', label: `示例库（${cfg.value?.examples.length ?? 0}）`, dirty: changes.value.some((c) => c.startsWith('示例')) },
  { key: 'D', label: '专家细则', dirty: LAYERS.filter((l) => l.group === 'D').some((l) => changes.value.includes(l.title)) },
  { key: 'P', label: '整段粘贴', dirty: changes.value.includes('整段 Prompt') },
  { key: 'E', label: '系统内置', dirty: false },
]);

function resetField(key) {
  if (defaults.value) cfg.value[key] = clone(defaults.value)[key];
}
function resetAll() {
  const d = defaults.value;
  if (d) {
    cfg.value = clone(d);
  } else {
    legacy.value = props.defaultText;
  }
}
function upgrade() {
  if (defaults.value) {
    cfg.value = clone(defaults.value);
    legacy.value = '';
  }
}
const rawOn = computed(() => hasRaw(cfg.value));
async function fillRaw() {
  try {
    const r = await api('/api/prompts/render', { method: 'POST', body: { content: stringify(cfg.value), bare: true } });
    cfg.value.raw = r.text;
  } catch (e) {
    err.value = `生成全文失败：${e.message}`;
  }
}
function addExample() {
  cfg.value.examples.push(emptyExample());
}
function moveExample(i, d) {
  const ex = cfg.value.examples;
  const j = i + d;
  if (j < 0 || j >= ex.length) return;
  [ex[i], ex[j]] = [ex[j], ex[i]];
}
async function loadPreview() {
  previewOpen.value = !previewOpen.value;
  if (!previewOpen.value) return;
  preview.value = null;
  try {
    preview.value = await api('/api/prompts/render', { method: 'POST', body: { content: draftText.value, duration_s: 600 } });
  } catch (e) {
    preview.value = { text: `预览失败：${e.message}`, system: '' };
  }
}
function close() {
  emit('update:open', false);
}
async function confirm() {
  const text = draftText.value;
  if (saveDefault.value && changes.value.length) {
    busy.value = true;
    err.value = '';
    try {
      await api('/api/prompts/default', { method: 'PUT', body: { content: text } });
      emit('saved-default', text);
    } catch (e) {
      err.value = `保存默认规则失败：${e.message}`;
      return;
    } finally {
      busy.value = false;
    }
  }
  emit('update:modelValue', text);
  emit('confirm', text);
  close();
}
</script>

<template>
  <div v-if="open" class="drawer-mask" @click.self="close">
    <aside class="drawer">
      <header>
        <div>
          <b>{{ title }}</b>
          <span v-if="!changes.length" class="tag">默认规则</span>
          <span v-else class="tag warn" :title="changes.join('、')">已修改 {{ changes.length }} 处</span>
          <div class="muted small">{{ hint }}</div>
        </div>
        <span class="spacer" />
        <button class="sm" @click="loadPreview">{{ previewOpen ? '返回编辑' : '预览发送给 AI 的内容' }}</button>
        <button class="sm" @click="close">✕</button>
      </header>

      <!-- 旧版纯文本规则 -->
      <div v-if="!cfg && !previewOpen" class="body col">
        <div class="legacy-tip small">
          这是旧版纯文本规则（v1）。可以直接编辑全文，或
          <button class="sm link" :disabled="!defaults" @click="upgrade">换成新版结构化默认规则</button>。
        </div>
        <textarea v-model="legacy" class="mono small grow" />
      </div>

      <div v-else-if="previewOpen" class="body col">
        <p class="muted small">以下为按当前设置渲染、实际发送给 AI 的规则正文（以 10 分钟视频为例），之后程序还会附加“系统内置”约定。</p>
        <pre class="preview small">{{ preview ? preview.text : '加载中…' }}</pre>
      </div>

      <div v-else class="body">
        <nav>
          <a v-for="t in tabs" :key="t.key" :class="{ on: tab === t.key }" @click="tab = t.key">
            <span><i class="layer">{{ t.key }}</i>{{ t.label }}</span><i v-if="t.dirty" class="dot" />
          </a>
        </nav>

        <section v-if="tab === 'A'" class="pane">
          <h3>基本设置</h3>
          <p class="muted small">这些设置程序和 AI 共用：例如题量密度既告诉 AI，也决定程序判断“题量是否不足、要不要回扫”。</p>
          <div class="field">
            <label>题量密度</label>
            <div class="row">
              <input v-model.number="cfg.settings.density" type="range" min="0.2" max="3" step="0.1" style="width: 240px" />
              <input v-model.number="cfg.settings.density" type="number" min="0.2" max="3" step="0.1" style="width: 70px" />
              <span class="muted small">题/分钟（10 分钟视频约 {{ Math.max(1, Math.ceil(10 * cfg.settings.density)) }} 题）</span>
            </div>
            <div class="muted small">整段平均值，质量优先；AI 不会为凑数收录不合格问题。</div>
          </div>
          <div class="field">
            <label>判断题</label>
            <label class="radio"><input v-model="cfg.settings.judgment" type="radio" :value="true" /> 收录（是/否、对/错、能/不能）</label>
            <label class="radio"><input v-model="cfg.settings.judgment" type="radio" :value="false" /> 不收录</label>
          </div>
          <div class="field">
            <label>作答时间点放在</label>
            <label v-for="o in ANCHOR_OPTIONS" :key="o.value" class="radio">
              <input v-model="cfg.settings.anchor" type="radio" :value="o.value" /> {{ o.label }} <span class="muted small">{{ o.desc }}</span>
            </label>
          </div>
          <div class="field">
            <label>答案写法</label>
            <div class="small">老师说出选项字母时只写字母（如“C”），不写选项内容 <span class="tag">固定规则</span></div>
          </div>
        </section>

        <section v-else-if="tab === 'B' || tab === 'D'" class="pane">
          <div v-if="rawOn" class="legacy-tip small">当前使用“整段粘贴”的 Prompt，本页内容不会发送给 AI。<button class="sm link" @click="tab = 'P'">去查看</button></div>
          <h3>{{ tab === 'B' ? '业务规则' : '专家细则' }}</h3>
          <p class="muted small">{{ tab === 'B' ? '教研可调整的核心规则。' : '一般无需修改，调整前建议先小范围试用。' }}</p>
          <div v-for="l in LAYERS.filter((x) => x.group === tab)" :key="l.key" class="field">
            <div class="row">
              <label>{{ l.title }}</label>
              <span class="muted small">{{ l.hint }}</span>
              <span v-if="changes.includes(l.title)" class="tag warn">已修改</span>
              <span class="spacer" />
              <button class="sm link" :disabled="!changes.includes(l.title)" @click="resetField(l.key)">恢复默认</button>
            </div>
            <textarea v-model="cfg[l.key]" class="small" :rows="l.key === 'b1' ? 16 : l.key === 'role' ? 3 : 8" />
          </div>
        </section>

        <section v-else-if="tab === 'C'" class="pane">
          <div v-if="rawOn" class="legacy-tip small">当前使用“整段粘贴”的 Prompt，本页内容不会发送给 AI。<button class="sm link" @click="tab = 'P'">去查看</button></div>
          <div class="row">
            <h3 style="margin: 0">示例库</h3>
            <span class="muted small">正例教 AI 怎么做，反例教 AI 不要做什么。具体、贴近真实课堂的示例效果最好。</span>
            <span class="spacer" />
            <button class="sm" :disabled="!changes.some((c) => c.startsWith('示例'))" @click="resetField('examples')">恢复默认示例</button>
            <button class="sm primary" @click="addExample">＋ 新增示例</button>
          </div>
          <div v-for="(ex, i) in cfg.examples" :key="i" class="example" :class="ex.kind">
            <div class="row">
              <select v-model="ex.kind" class="sm-select">
                <option value="positive">正例</option>
                <option value="negative">反例</option>
              </select>
              <input v-model="ex.title" placeholder="标题，如：选择题·字母优先" style="flex: 1" />
              <button class="sm" :disabled="i === 0" title="上移" @click="moveExample(i, -1)">↑</button>
              <button class="sm" :disabled="i === cfg.examples.length - 1" title="下移" @click="moveExample(i, 1)">↓</button>
              <button class="sm danger" @click="cfg.examples.splice(i, 1)">删除</button>
            </div>
            <label class="ex-l">老师原话<textarea v-model="ex.original" rows="2" class="small" /></label>
            <label class="ex-l">正确做法<textarea v-model="ex.correct" rows="2" class="small" /></label>
            <div class="row" style="align-items: flex-start">
              <label class="ex-l" style="flex: 1">错误做法（选填）<textarea v-model="ex.wrong" rows="2" class="small" /></label>
              <label class="ex-l" style="flex: 1">说明（选填）<textarea v-model="ex.note" rows="2" class="small" /></label>
            </div>
          </div>
          <div v-if="!cfg.examples.length" class="muted">暂无示例</div>
        </section>

        <section v-else-if="tab === 'P'" class="pane col-pane">
          <div class="row">
            <h3 style="margin: 0">整段粘贴</h3>
            <span v-if="rawOn" class="tag warn">已启用</span>
            <span class="spacer" />
            <button class="sm" @click="fillRaw">用当前分层规则生成全文</button>
            <button class="sm" :disabled="!rawOn" @click="cfg.raw = ''">清空（改回分层规则）</button>
          </div>
          <p class="muted small">
            把自己编辑好的完整 Prompt 粘到这里即可。填写后它将替代“业务规则、示例库、专家细则”发送给 AI；
            “基本设置”（题量、判断题、时间点）和答案字母优先规则仍会附加在末尾并优先生效，系统内置的流程与输出格式也照常由程序保证。清空即恢复使用分层规则。
          </p>
          <textarea v-model="cfg.raw" class="small grow raw" placeholder="在此粘贴完整 Prompt……" />
        </section>

        <section v-else class="pane">
          <h3>系统内置</h3>
          <p class="muted small">以下由程序保证，不在规则中编辑，列出供了解：</p>
          <ol class="small sys">
            <li><b>扫描流程</b>：第 1 轮候选发现 → 程序列出尚未判断的互动指令与长停顿 → 第 2 轮逐个判断（题量不足时完整回扫）→ 第 3 轮逐题复核。</li>
            <li><b>输入格式</b>：带分段编号和时间的逐字稿；“&lt;停顿 Xs&gt;”为程序检测到的静音。</li>
            <li><b>输出格式</b>：时间点只输出“锚点分段 + 最后几个字”，由程序换算为 HH:MM:SS.mmm 并对齐到附近停顿。</li>
            <li><b>复核要求</b>：第 3 轮按业务规则、专家细则和示例逐题检查，并做反向核验。</li>
            <li><b>程序校验</b>：原话匹配度、时间越界、锚点后停顿、相邻题过近、答案规范化（“C选项”→“C”）、空值；设置为不收录判断题时，判断题答案会被标记待复核。</li>
          </ol>
        </section>
      </div>

      <footer>
        <button :disabled="!changes.length" @click="resetAll">全部恢复默认</button>
        <span v-if="changes.length" class="muted small changes" :title="changes.join('、')">已修改：{{ changes.join('、') }}</span>
        <span class="spacer" />
        <span v-if="err" class="small err">{{ err }}</span>
        <label v-if="allowSaveDefault && changes.length" class="small save-default" title="保存后成为新的默认规则版本，之后新建的任务都会使用">
          <input v-model="saveDefault" type="checkbox" /> 同时保存为默认规则
        </label>
        <button @click="close">取消</button>
        <button class="primary" :disabled="busy || (cfg ? !cfg.b1.trim() && !rawOn : legacy.trim().length < 20)" @click="confirm">{{ confirmText }}</button>
      </footer>
    </aside>
  </div>
</template>

<style scoped>
.drawer-mask { position: fixed; inset: 0; background: rgba(0, 0, 0, .3); z-index: 60; display: flex; justify-content: flex-end; }
.drawer { width: min(1000px, 96vw); height: 100%; background: #fff; display: flex; flex-direction: column; box-shadow: -8px 0 24px rgba(0, 0, 0, .12); }
.drawer header { display: flex; align-items: flex-start; gap: 10px; padding: 14px 18px; border-bottom: 1px solid var(--border); }
.drawer header b { font-size: 15px; margin-right: 8px; }
.body { flex: 1; min-height: 0; padding: 14px 18px; display: flex; gap: 16px; }
.body.col { flex-direction: column; gap: 8px; }
.grow { flex: 1; resize: none; }
.col-pane { display: flex; flex-direction: column; gap: 8px; }
.raw { min-height: 360px; line-height: 1.65; }
.save-default { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; white-space: nowrap; }
.err { color: var(--danger); }
nav { width: 170px; flex-shrink: 0; border-right: 1px solid var(--border); padding-right: 8px; }
nav a { display: flex; align-items: center; justify-content: space-between; padding: 9px 10px; border-radius: 6px; color: var(--text); cursor: pointer; text-decoration: none; }
nav a:hover { background: #f3f4f6; }
nav a.on { background: var(--primary-weak); color: var(--primary); font-weight: 600; }
.layer { display: inline-flex; width: 18px; height: 18px; border-radius: 4px; background: #eef0f3; color: var(--muted); font-size: 11px; font-style: normal; align-items: center; justify-content: center; margin-right: 8px; }
nav a.on .layer { background: var(--primary); color: #fff; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--warn); }
.pane { flex: 1; min-width: 0; overflow: auto; padding-right: 4px; }
.pane h3 { margin: 0 0 4px; font-size: 15px; }
.field { margin: 16px 0; }
.field > label, .field .row > label { font-weight: 600; display: block; margin-bottom: 6px; }
.radio { display: block; margin: 4px 0; cursor: pointer; }
.field textarea { line-height: 1.65; }
.example { border: 1px solid var(--border); border-left: 3px solid var(--ok); border-radius: 8px; padding: 10px; margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.example.negative { border-left-color: var(--danger); }
.ex-l { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--muted); }
.sm-select { height: 28px; }
.sys { line-height: 1.9; padding-left: 20px; }
.preview { flex: 1; overflow: auto; white-space: pre-wrap; background: #fafafa; border: 1px solid var(--border); border-radius: 6px; padding: 12px; margin: 0; line-height: 1.65; }
.legacy-tip { background: var(--warn-bg); color: var(--warn); padding: 8px 12px; border-radius: 6px; }
footer { display: flex; align-items: center; gap: 8px; padding: 12px 18px; border-top: 1px solid var(--border); }
.changes { max-width: 460px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
