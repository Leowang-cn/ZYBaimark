<script setup>
import { computed, inject, onMounted, ref } from 'vue';
import { api } from '../api';
import { diffRules, isStructured, summarize } from '../rules';
import RulesDrawer from '../components/RulesDrawer.vue';

const toast = inject('toast');
const versions = ref([]);
const draft = ref('');
const editing = ref(false);
const viewing = ref(null);

const current = computed(() => versions.value[0]);

async function load() {
  versions.value = await api('/api/prompts/default/versions');
}
onMounted(() => load().catch((e) => toast(e.message, true)));

function edit(base) {
  draft.value = base.content;
  editing.value = true;
}

async function save(content) {
  try {
    const r = await api('/api/prompts/default', { method: 'PUT', body: { content } });
    toast(r.unchanged ? '内容未变化' : `已保存为默认规则 v${r.version}`);
    await load();
  } catch (e) {
    toast(e.message, true);
  }
}

function changesFrom(v) {
  const prev = versions.value.find((x) => x.version < v.version);
  return prev ? diffRules(v.content, prev.content) : [];
}
</script>

<template>
  <div class="page" style="max-width: 960px">
    <div class="row" style="margin-bottom: 16px">
      <h1 style="margin: 0">默认抽题规则</h1>
      <span class="spacer" />
      <button class="primary" :disabled="!current" @click="edit(current)">编辑默认规则</button>
    </div>
    <p class="muted small">新建任务时默认使用这里的规则。保存会生成新版本，已创建的任务不受影响；可随时基于历史版本恢复。</p>

    <div v-if="current" class="panel">
      <div class="row">
        <b>当前：v{{ current.version }}</b>
        <span class="tag" :class="isStructured(current.content) ? 'done' : 'warn'">{{ isStructured(current.content) ? '结构化规则' : '旧版纯文本' }}</span>
        <span class="muted small">{{ new Date(current.created_at + 'Z').toLocaleString() }}</span>
      </div>
      <p class="small" style="margin: 8px 0 0">{{ summarize(current.content) }}</p>
    </div>

    <div class="panel">
      <h2>历史版本</h2>
      <table class="list">
        <thead><tr><th>版本</th><th>时间</th><th>类型</th><th>相对上一版的改动</th><th></th></tr></thead>
        <tbody>
          <tr v-for="v in versions" :key="v.version">
            <td><b>v{{ v.version }}</b><span v-if="v === current" class="tag done" style="margin-left: 6px">当前</span></td>
            <td class="muted small">{{ new Date(v.created_at + 'Z').toLocaleString() }}</td>
            <td class="small">{{ isStructured(v.content) ? '结构化' : '纯文本' }}</td>
            <td class="small">{{ changesFrom(v).join('、') || '—' }}</td>
            <td class="row">
              <button class="sm" @click="viewing = v">查看</button>
              <button v-if="v !== current" class="sm" @click="edit(v)">基于此版本编辑</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <RulesDrawer v-model:open="editing" v-model="draft" :default-text="current?.content || ''"
      title="编辑默认规则" hint="保存后成为新的默认规则（新版本），只影响之后新建的任务。" confirm-text="保存为新版本" @confirm="save" />
    <RulesDrawer v-if="viewing" :open="true" :model-value="viewing.content" :default-text="current?.content || ''"
      :title="`默认规则 v${viewing.version}（只读查看）`" hint="可预览内容；如需恢复，请关闭后点“基于此版本编辑”。" confirm-text="关闭"
      read-only @update:open="viewing = null" @confirm="viewing = null" />
  </div>
</template>
