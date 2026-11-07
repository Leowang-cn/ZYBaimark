<script setup>
// 获取内容生产系统登录态：服务器支持时可钉钉扫码，否则粘贴 Cookie（粘贴后即时校验）
import { onMounted, onUnmounted, ref } from 'vue';
import { api } from '../api';

const emit = defineEmits(['change']);
const CTP_URL = 'https://iot-admin.yukework.com/fe-ctpsystem/#/material';

const qrSupported = ref(false);
const mode = ref('cookie');
const cookieText = ref('');
const check = ref({ state: 'idle', uname: '', reason: '' }); // idle | checking | ok | bad
const showHelp = ref(false);
const session = ref(null);
const error = ref('');
let timer;
let checkTimer;
let checkSeq = 0;

function emitState() {
  if (mode.value === 'qr') {
    emit('change', session.value?.status === 'success' ? { login_session_id: session.value.id } : null);
  } else {
    emit('change', check.value.state === 'ok' ? { cookie_text: cookieText.value } : null);
  }
}

onMounted(async () => {
  try {
    const c = await api('/api/capabilities');
    qrSupported.value = c.qr_login === true;
  } catch { /* 默认粘贴 Cookie */ }
});

function onPaste() {
  clearTimeout(checkTimer);
  check.value = { state: 'idle', uname: '', reason: '' };
  emitState();
  if (!cookieText.value.trim()) return;
  checkTimer = setTimeout(runCheck, 400);
}

async function runCheck() {
  const seq = ++checkSeq;
  check.value = { state: 'checking', uname: '', reason: '' };
  try {
    const r = await api('/api/ctp/check', { method: 'POST', body: { cookie_text: cookieText.value } });
    if (seq !== checkSeq) return;
    check.value = r.ok ? { state: 'ok', uname: r.uname, reason: '' } : { state: 'bad', uname: '', reason: r.reason };
  } catch (e) {
    if (seq !== checkSeq) return;
    check.value = { state: 'bad', uname: '', reason: e.message };
  }
  if (check.value.state === 'bad') showHelp.value = true;
  emitState();
}

function clearCookie() {
  cookieText.value = '';
  check.value = { state: 'idle', uname: '', reason: '' };
  emitState();
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

async function startQr() {
  clearTimeout(timer);
  error.value = '';
  try {
    session.value = await api('/api/login-sessions', { method: 'POST' });
    emitState();
    poll();
  } catch (e) {
    error.value = e.message;
  }
}

function refreshQr() {
  api(`/api/login-sessions/${session.value.id}/refresh`, { method: 'POST' }).catch((e) => (error.value = e.message));
}

function switchMode(m) {
  mode.value = m;
  if (m === 'qr' && !session.value) startQr();
  emitState();
}

onUnmounted(() => {
  clearTimeout(timer);
  clearTimeout(checkTimer);
  if (session.value && ['starting', 'pending'].includes(session.value.status)) {
    api(`/api/login-sessions/${session.value.id}`, { method: 'DELETE' }).catch(() => {});
  }
});
</script>

<template>
  <div class="ctp-login">
    <div v-if="qrSupported" class="row" style="margin-bottom: 10px">
      <button class="sm" :class="{ primary: mode === 'cookie' }" @click="switchMode('cookie')">粘贴 Cookie</button>
      <button class="sm" :class="{ primary: mode === 'qr' }" @click="switchMode('qr')">钉钉扫码</button>
    </div>

    <template v-if="mode === 'cookie'">
      <div v-if="check.state === 'ok'" class="ok-box row">
        <span class="ok-icon">✓</span>
        <span>已识别登录账号 <b>{{ check.uname || '（未知）' }}</b></span>
        <span class="spacer" />
        <button class="sm link" @click="clearCookie">更换</button>
      </div>
      <template v-else>
        <textarea v-model="cookieText" rows="2" class="mono small"
          placeholder="把从内容生产系统复制的 Cookie 粘贴到这里" @input="onPaste" />
        <div class="row small status">
          <span v-if="check.state === 'checking'" class="muted">正在校验…</span>
          <span v-else-if="check.state === 'bad'" class="err">{{ check.reason }}</span>
          <span v-else class="muted">登录信息只用于本次任务解析视频地址，24 小时后自动删除。</span>
          <span class="spacer" />
          <button class="sm link" @click="showHelp = !showHelp">{{ showHelp ? '收起说明' : '如何获取 Cookie？' }}</button>
        </div>
        <div v-if="showHelp" class="help small">
          <ol>
            <li>在 Chrome 中打开 <a :href="CTP_URL" target="_blank" rel="noopener">内容生产系统</a>，确认已登录。</li>
            <li>按 <kbd>F12</kbd>（Mac 按 <kbd>⌥</kbd><kbd>⌘</kbd><kbd>I</kbd>）打开开发者工具，点顶部的 <b>Network（网络）</b>，然后按 <kbd>F5</kbd> 刷新页面。</li>
            <li>在左侧列表中点任意一条请求（例如 <code>list</code> 或 <code>info</code>），右侧找到 <b>Request Headers</b> 里的 <b>Cookie</b>，复制它后面的整段内容。</li>
            <li>粘贴到上面的输入框，系统会自动校验并显示你的账号名。</li>
          </ol>
          <p class="muted">也可以在请求上右键 → Copy → Copy as cURL，整段粘贴进来同样能识别。</p>
        </div>
      </template>
    </template>

    <template v-else>
      <div v-if="!session || session.status === 'starting'" class="muted">正在打开登录页，约 5 秒…</div>
      <div v-else-if="session.status === 'pending'" class="row" style="align-items: flex-start">
        <img v-if="session.qr" :src="`data:image/png;base64,${session.qr}`" width="180" alt="钉钉登录二维码" />
        <div>
          <p>请用<b>钉钉</b>扫码并在手机上确认。</p>
          <button class="sm" @click="refreshQr">刷新二维码</button>
        </div>
      </div>
      <div v-else-if="session.status === 'success'" class="ok-box row"><span class="ok-icon">✓</span> 扫码登录成功</div>
      <div v-else class="row">
        <span class="err">{{ session.error || '二维码已过期' }}</span>
        <button class="sm" @click="startQr">重新获取</button>
      </div>
    </template>
    <p v-if="error" class="err small">{{ error }}</p>
  </div>
</template>

<style scoped>
.status { margin-top: 6px; }
.ok-box { background: var(--ok-bg); color: var(--ok); border-radius: 6px; padding: 10px 12px; }
.ok-box b { color: var(--text); }
.ok-icon { display: inline-flex; width: 20px; height: 20px; border-radius: 50%; background: var(--ok); color: #fff; align-items: center; justify-content: center; font-size: 12px; flex-shrink: 0; }
.help { margin-top: 8px; background: #f8fafc; border: 1px solid var(--border); border-radius: 6px; padding: 8px 14px; }
.help ol { margin: 4px 0; padding-left: 18px; line-height: 1.9; }
.help p { margin: 4px 0; }
kbd { border: 1px solid var(--border); border-bottom-width: 2px; border-radius: 4px; padding: 0 5px; font-size: 11px; background: #fff; }
</style>
