<script setup>
import { ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api } from '../api';

const route = useRoute();
const router = useRouter();
const password = ref('');
const error = ref('');
const loading = ref(false);

async function submit() {
  error.value = '';
  loading.value = true;
  try {
    await api('/api/auth/login', { method: 'POST', body: { password: password.value } });
    const next = typeof route.query.next === 'string' && route.query.next.startsWith('/') ? route.query.next : '/tasks';
    router.replace(next);
  } catch (e) {
    error.value = e.message;
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="login-wrap">
    <form class="panel login" @submit.prevent="submit">
      <h1>小数 AI 课自动打点</h1>
      <label class="field">
        <span>访问口令</span>
        <input v-model="password" type="password" autocomplete="current-password" autofocus style="width: 100%" />
      </label>
      <p v-if="error" class="err">{{ error }}</p>
      <button class="primary" type="submit" :disabled="!password || loading" style="width: 100%">进入</button>
    </form>
  </div>
</template>

<style scoped>
.login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; }
.login { width: 360px; padding: 28px; }
</style>
