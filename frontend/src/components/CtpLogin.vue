<script setup>
// 获取内容生产系统登录态：钉钉扫码（默认）或粘贴 Cookie（兜底）
import { onUnmounted, ref } from 'vue';
import { api } from '../api';

const emit = defineEmits(['change']);
const mode = ref('qr');
const session = ref(null);
const cookieText = ref('');
const error = ref('');
let timer;

function emitState() {
  if (mode.value === 'qr') {
    emit('change', session.value?.status === 'success' ? { login_session_id: session.value.id } : null);
  } else {
    emit('change', /ZYBIPSCAS=/.test(cookieText.value) ? { cookie_text: cookieText.value } : null);
  }
}

async function poll() {
  if (!session.value) return;
  try {
    session.value = await api(`/api/login-sessions/${session.value.id}`);
  } catch (e) {
    error.value = e.message;
  }
  emitState();
  if (['starting', 'pending'].includes(session.value?.status)) timer = setTimeout(poll, 1500);
}

async function start() {
  clearTimeout(timer);
  error.value = '';
  if (session.value && ['starting', 'pending'].includes(session.value.status)) {
    api(`/api/login-sessions/${session.value.id}`, { method: 'DELETE' }).catch(() => {});
  }
  try {
    session.value = await api('/api/login-sessions', { method: 'POST' });
    emitState();
    poll();
  } catch (e) {
    error.value = e.message;
  }
}

async function refresh() {
  await api(`/api/login-sessions/${session.value.id}/refresh`, { method: 'POST' }).catch((e) => (error.value = e.message));
}

function switchMode(m) {
  mode.value = m;
  emitState();
}

function reset() {
  session.value = null;
  cookieText.value = '';
  emitState();
}
defineExpose({ reset });

onUnmounted(() => {
  clearTimeout(timer);
  if (session.value && ['starting', 'pending'].includes(session.value.status)) {
    api(`/api/login-sessions/${session.value.id}`, { method: 'DELETE' }).catch(() => {});
  }
});
</script>

<template>
  <div class="ctp-login">
    <div class="row" style="margin-bottom: 10px">
      <button class="sm" :class="{ primary: mode === 'qr' }" @click="switchMode('qr')">钉钉扫码</button>
      <button class="sm" :class="{ primary: mode === 'cookie' }" @click="switchMode('cookie')">粘贴 Cookie</button>
    </div>

    <div v-if="mode === 'qr'">
      <div v-if="!session || ['expired', 'error', 'cancelled', 'used'].includes(session.status)" class="row">
        <button @click="start">获取登录二维码</button>
        <span v-if="session?.status === 'expired'" class="muted">二维码已过期</span>
        <span v-if="session?.status === 'error'" class="err">{{ session.error }}</span>
      </div>
      <div v-else-if="session.status === 'starting'" class="muted">正在打开登录页，约 5 秒…</div>
      <div v-else-if="session.status === 'pending'" class="row" style="align-items: flex-start">
        <img v-if="session.qr" :src="`data:image/png;base64,${session.qr}`" width="200" alt="钉钉登录二维码" />
        <div>
          <p>请用<b>钉钉</b>扫码，并在手机上确认登录。</p>
          <p class="muted small" v-if="session.qr_expires_in !== null">二维码约 {{ session.qr_expires_in }} 秒后过期</p>
          <button class="sm" @click="refresh">刷新二维码</button>
        </div>
      </div>
      <div v-else-if="session.status === 'success'" class="row">
        <span class="tag done">登录成功</span>
        <span class="muted small">登录态仅用于本次任务</span>
      </div>
    </div>

    <div v-else>
      <textarea v-model="cookieText" rows="3" placeholder="在已登录内容生产系统的浏览器中按 F12 → Network → 任一请求 → 复制 Cookie 或 Copy as cURL，粘贴到这里"
        @input="emitState" />
      <p class="muted small">只会提取其中的 ZYBIPSCAS，加密保存且 24 小时后删除。</p>
    </div>
    <p v-if="error" class="err">{{ error }}</p>
  </div>
</template>
