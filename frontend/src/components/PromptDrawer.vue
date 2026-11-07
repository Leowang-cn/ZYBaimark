<script setup>
// 抽题规则抽屉：按章节分字段编辑，也可切换为全文编辑
import { computed, ref, watch } from 'vue';
import { changedTitles, joinPrompt, splitPrompt } from '../prompt';

const props = defineProps({
  open: Boolean,
  modelValue: { type: String, default: '' },
  defaultText: { type: String, default: '' },
  title: { type: String, default: '抽题规则' },
  hint: { type: String, default: '修改只对本次任务生效，不影响默认规则。' },
  confirmText: { type: String, default: '使用这份规则' },
});
const emit = defineEmits(['update:open', 'update:modelValue', 'confirm']);

const draft = ref('');
const chunks = ref([]);
const active = ref(0);
const mode = ref('sections');

watch(() => props.open, (o) => {
  if (!o) return;
  draft.value = props.modelValue;
  chunks.value = splitPrompt(draft.value);
  active.value = 0;
  mode.value = 'sections';
});

const changed = computed(() => changedTitles(draft.value, props.defaultText));
const isDefault = computed(() => draft.value.trim() === props.defaultText.trim());
const cur = computed(() => chunks.value[active.value]);
const defaultBody = computed(() => splitPrompt(props.defaultText).find((c) => c.title === cur.value?.title)?.body);

function editBody(v) {
  cur.value.body = v;
  draft.value = joinPrompt(chunks.value);
}
function editAll(v) {
  draft.value = v;
  chunks.value = splitPrompt(v);
  active.value = Math.min(active.value, chunks.value.length - 1);
}
function resetSection() {
  if (defaultBody.value !== undefined) editBody(defaultBody.value);
}
function resetAll() {
  editAll(props.defaultText);
}
function close() {
  emit('update:open', false);
}
function confirm() {
  emit('update:modelValue', draft.value);
  emit('confirm', draft.value);
  close();
}
</script>

<template>
  <div v-if="open" class="drawer-mask" @click.self="close">
    <aside class="drawer">
      <header>
        <div>
          <b>{{ title }}</b>
          <span v-if="isDefault" class="tag">默认规则</span>
          <span v-else class="tag warn">已修改 {{ changed.size }} 处</span>
          <div class="muted small">{{ hint }}</div>
        </div>
        <span class="spacer" />
        <div class="seg">
          <button class="sm" :class="{ primary: mode === 'sections' }" @click="mode = 'sections'">按章节</button>
          <button class="sm" :class="{ primary: mode === 'full' }" @click="mode = 'full'">全文</button>
        </div>
        <button class="sm" @click="close">✕</button>
      </header>

      <div v-if="mode === 'sections'" class="body sections">
        <nav>
          <a v-for="(c, i) in chunks" :key="c.key" :class="{ on: i === active }" @click="active = i">
            <span>{{ c.title }}</span>
            <i v-if="changed.has(c.title)" class="dot" title="已修改" />
          </a>
        </nav>
        <section v-if="cur">
          <div class="row">
            <b>{{ cur.title }}</b>
            <span class="spacer" />
            <button class="sm" :disabled="!changed.has(cur.title) || defaultBody === undefined" @click="resetSection">恢复本节默认</button>
          </div>
          <textarea class="mono small" :value="cur.body" @input="editBody($event.target.value)" />
        </section>
      </div>
      <div v-else class="body">
        <textarea class="mono small full" :value="draft" @input="editAll($event.target.value)" />
      </div>

      <footer>
        <button :disabled="isDefault" @click="resetAll">全部恢复默认</button>
        <span class="spacer" />
        <button @click="close">取消</button>
        <button class="primary" :disabled="draft.trim().length < 20" @click="confirm">{{ confirmText }}</button>
      </footer>
    </aside>
  </div>
</template>

<style scoped>
.drawer-mask { position: fixed; inset: 0; background: rgba(0, 0, 0, .3); z-index: 60; display: flex; justify-content: flex-end; }
.drawer { width: min(980px, 96vw); height: 100%; background: #fff; display: flex; flex-direction: column; box-shadow: -8px 0 24px rgba(0, 0, 0, .12); }
.drawer header { display: flex; align-items: flex-start; gap: 10px; padding: 14px 18px; border-bottom: 1px solid var(--border); }
.drawer header b { font-size: 15px; margin-right: 8px; }
.seg { display: flex; gap: 4px; }
.body { flex: 1; min-height: 0; padding: 14px 18px; display: flex; }
.sections { gap: 14px; }
nav { width: 190px; flex-shrink: 0; overflow: auto; border-right: 1px solid var(--border); padding-right: 8px; }
nav a { display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; border-radius: 6px; color: var(--text); cursor: pointer; text-decoration: none; }
nav a:hover { background: #f3f4f6; }
nav a.on { background: var(--primary-weak); color: var(--primary); font-weight: 600; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--warn); }
section { flex: 1; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
section textarea, .full { flex: 1; resize: none; line-height: 1.65; }
footer { display: flex; gap: 8px; padding: 12px 18px; border-top: 1px solid var(--border); }
</style>
