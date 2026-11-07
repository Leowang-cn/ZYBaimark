<script setup>
import { provide, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api, setUnauthorizedHandler } from './api';

const route = useRoute();
const router = useRouter();
const toastMsg = ref('');
const toastErr = ref(false);
let timer;

function toast(msg, error = false) {
  // 未登录会自动跳转登录页，无需再提示
  if (error && msg === '请先登录') return;
  toastMsg.value = msg;
  toastErr.value = error;
  clearTimeout(timer);
  timer = setTimeout(() => (toastMsg.value = ''), error ? 5000 : 2500);
}
provide('toast', toast);

setUnauthorizedHandler(() => {
  if (route.path !== '/login') router.replace({ path: '/login', query: { next: route.fullPath } });
});

async function logout() {
  await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
  router.replace('/login');
}
</script>

<template>
  <header v-if="!route.meta.public" class="topbar">
    <span class="brand">小数 AI 课自动打点</span>
    <nav>
      <router-link to="/tasks">任务</router-link>
      <router-link to="/tasks/new">新建任务</router-link>
      <router-link to="/settings">默认规则</router-link>
    </nav>
    <button class="sm" @click="logout">退出</button>
  </header>
  <router-view />
  <div v-if="toastMsg" class="toast" :class="{ error: toastErr }">{{ toastMsg }}</div>
</template>
