<script setup>
import { onMounted, onUnmounted, ref } from 'vue';
import { api } from '../api';

const tasks = ref([]);
const error = ref('');
let timer;

async function load() {
  try {
    tasks.value = await api('/api/tasks');
    error.value = '';
  } catch (e) {
    error.value = e.message;
  }
}
onMounted(() => {
  load();
  timer = setInterval(load, 5000);
});
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <div class="page">
    <div class="row" style="margin-bottom: 16px">
      <h1 style="margin: 0">任务</h1>
      <span class="spacer" />
      <router-link to="/tasks/new"><button class="primary">新建任务</button></router-link>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
    <div class="panel">
      <table class="list">
        <thead>
          <tr><th>任务</th><th>创建时间</th><th>视频数</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id">
            <td><router-link :to="`/tasks/${t.id}`">{{ t.name }}</router-link></td>
            <td class="muted">{{ new Date(t.created_at + 'Z').toLocaleString() }}</td>
            <td>{{ t.total }}</td>
            <td class="row">
              <span v-if="t.done" class="tag done">完成 {{ t.done }}</span>
              <span v-if="t.running" class="tag running">处理中 {{ t.running }}</span>
              <span v-if="t.failed" class="tag failed">失败 {{ t.failed }}</span>
            </td>
          </tr>
          <tr v-if="!tasks.length"><td colspan="4" class="muted">还没有任务</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
