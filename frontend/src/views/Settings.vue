<script setup>
import { inject, onMounted, ref } from 'vue';
import { api } from '../api';
import PromptEditor from '../components/PromptEditor.vue';

const toast = inject('toast');
const versions = ref([]);
const content = ref('');
const viewing = ref(null);

async function load() {
  versions.value = await api('/api/prompts/default/versions');
  content.value = versions.value[0]?.content || '';
}
onMounted(() => load().catch((e) => toast(e.message, true)));

async function save() {
  try {
    const r = await api('/api/prompts/default', { method: 'PUT', body: { content: content.value } });
    toast(r.unchanged ? '内容未变化' : `已保存为默认规则 v${r.version}`);
    await load();
  } catch (e) {
    toast(e.message, true);
  }
}
</script>

<template>
  <div class="page">
    <div class="row" style="margin-bottom: 16px">
      <h1 style="margin: 0">默认抽题规则</h1>
      <span class="spacer" />
      <button class="primary" :disabled="content.length < 20" @click="save">保存为新版本</button>
    </div>
    <div class="row" style="align-items: flex-start; gap: 16px">
      <div class="panel" style="flex: 1">
        <p class="muted small" style="margin-top: 0">新建任务时默认填入此规则。保存会生成新版本，已创建的任务不受影响。</p>
        <PromptEditor v-model="content" :default-text="versions[0]?.content || ''" :rows="28" />
      </div>
      <div class="panel" style="width: 260px">
        <h2>历史版本</h2>
        <div v-for="v in versions" :key="v.version" class="row small" style="margin-bottom: 8px">
          <b>v{{ v.version }}</b>
          <span class="muted">{{ new Date(v.created_at + 'Z').toLocaleString() }}</span>
          <span class="spacer" />
          <button class="sm" @click="viewing = v">查看</button>
        </div>
      </div>
    </div>
    <div v-if="viewing" class="modal-mask" @click.self="viewing = null">
      <div class="modal">
        <header>默认规则 v{{ viewing.version }}</header>
        <div class="body"><pre class="small" style="white-space: pre-wrap">{{ viewing.content }}</pre></div>
        <footer>
          <button @click="viewing = null">关闭</button>
          <button class="primary" @click="content = viewing.content; viewing = null">载入到编辑器</button>
        </footer>
      </div>
    </div>
  </div>
</template>
