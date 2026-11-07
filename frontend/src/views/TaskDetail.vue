<script setup>
import { inject, onMounted, onUnmounted, ref } from 'vue';
import { api, fmtTs, STAGE_LABEL, STATUS_LABEL } from '../api';
import CtpLogin from '../components/CtpLogin.vue';
import RulesDrawer from '../components/RulesDrawer.vue';

const props = defineProps({ id: String });
const toast = inject('toast');
const task = ref(null);
const error = ref('');
const regenOpen = ref(false);
const regenPrompt = ref('');
const defaultPrompt = ref('');
const resolveFor = ref(null);
const resolveCred = ref(null);
let timer;

async function load() {
  try {
    task.value = await api(`/api/tasks/${props.id}`);
    error.value = '';
  } catch (e) {
    error.value = e.message;
  }
}

onMounted(async () => {
  await load();
  timer = setInterval(load, 3000);
  api('/api/prompts/default').then((p) => (defaultPrompt.value = p.content)).catch(() => {});
});
onUnmounted(() => clearInterval(timer));

function download(format) {
  window.location.href = `/api/export?task_id=${props.id}&format=${format}`;
}

async function checkAndDownload(format) {
  let unconfirmed = 0;
  let total = 0;
  for (const v of task.value.videos) {
    if (!v.current_generation_id) continue;
    const g = await api(`/api/generations/${v.current_generation_id}`);
    const live = g.marks.filter((m) => !m.deleted);
    total += live.length;
    unconfirmed += live.filter((m) => !m.confirmed).length;
  }
  if (!total) return toast('还没有可导出的结果', true);
  if (unconfirmed && !confirm(`还有 ${unconfirmed} 条打点未确认，仍然导出全部 ${total} 条未删除的打点吗？`)) return;
  download(format);
}

function openRegen() {
  regenPrompt.value = task.value.prompt_text;
  regenOpen.value = true;
}

async function regenAll() {
  try {
    const r = await api(`/api/tasks/${props.id}/generations`, { method: 'POST', body: { prompt_text: regenPrompt.value } });
    toast(`已提交 ${r.queued} 个视频重新生成`);
    regenOpen.value = false;
    load();
  } catch (e) {
    toast(e.message, true);
  }
}

async function retry(v) {
  try {
    await api(`/api/videos/${v.id}/retry`, { method: 'POST' });
    load();
  } catch (e) {
    toast(e.message, true);
  }
}

async function reresolve(v, cred) {
  try {
    await api(`/api/videos/${v.id}/resolve`, { method: 'POST', body: cred || {} });
    toast('解析成功，已加入队列');
    resolveFor.value = null;
    load();
  } catch (e) {
    if (!cred) resolveFor.value = v;
    else toast(e.message, true);
  }
}
</script>

<template>
  <div class="page">
    <p v-if="error" class="err">{{ error }}</p>
    <template v-if="task">
      <div class="row" style="margin-bottom: 16px">
        <h1 style="margin: 0">{{ task.name }}</h1>
        <span class="spacer" />
        <button @click="openRegen">修改规则并全部重新生成</button>
        <button @click="checkAndDownload('csv')">导出 CSV</button>
        <button class="primary" @click="checkAndDownload('xlsx')">导出 Excel</button>
      </div>
      <div class="panel">
        <table class="list">
          <thead>
            <tr><th>素材 ID</th><th>名称</th><th>时长</th><th>状态</th><th>版本</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="v in task.videos" :key="v.id">
              <td class="mono">{{ v.material_id }}</td>
              <td>{{ v.name || '—' }}</td>
              <td class="mono">{{ v.duration_s ? fmtTs(v.duration_s).slice(0, 8) : '—' }}</td>
              <td>
                <span class="tag" :class="v.status">{{ STATUS_LABEL[v.status] || v.status }}</span>
                <span v-if="v.status === 'running'" class="muted small"> {{ STAGE_LABEL[v.stage] || v.stage }}</span>
                <div v-if="v.error" class="err small">{{ v.error }}</div>
              </td>
              <td>{{ v.generation_count || '—' }}</td>
              <td class="row">
                <router-link v-if="v.current_generation_id" :to="`/videos/${v.id}`"><button class="sm primary">审阅</button></router-link>
                <button v-if="v.status === 'failed'" class="sm" @click="retry(v)">重试</button>
                <button v-if="v.status === 'resolve_failed'" class="sm" @click="reresolve(v)">重新解析</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <RulesDrawer v-model:open="regenOpen" v-model="regenPrompt" :default-text="defaultPrompt"
      title="修改规则并重新生成" hint="对本任务全部视频重新生成，每个视频产生新版本；已人工修改的版本不会被覆盖。" confirm-text="开始重新生成" @confirm="regenAll" />

    <div v-if="resolveFor" class="modal-mask" @click.self="resolveFor = null">
      <div class="modal" style="width: min(560px, 94vw)">
        <header>重新登录以解析 {{ resolveFor.material_id }}</header>
        <div class="body"><CtpLogin @change="resolveCred = $event" /></div>
        <footer>
          <button @click="resolveFor = null">取消</button>
          <button class="primary" :disabled="!resolveCred" @click="reresolve(resolveFor, resolveCred)">解析</button>
        </footer>
      </div>
    </div>
  </div>
</template>
