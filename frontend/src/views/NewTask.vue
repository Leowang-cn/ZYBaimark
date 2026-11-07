<script setup>
import { computed, inject, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { api } from '../api';
import { diffRules, summarize } from '../rules';
import CtpLogin from '../components/CtpLogin.vue';
import RulesDrawer from '../components/RulesDrawer.vue';

const router = useRouter();
const toast = inject('toast');
const name = ref('');
const idsText = ref('');
const prompt = ref('');
const defaultPrompt = ref('');
const defaultVersion = ref(null);
const cred = ref(null);
const drawer = ref(false);
const submitting = ref(false);
const error = ref('');
const result = ref(null);

const tokens = computed(() => idsText.value.split(/[\s,，;；、]+/).filter(Boolean));
const ids = computed(() => [...new Set(tokens.value)]);
const badIds = computed(() => ids.value.filter((x) => !/^\d{6,25}$/.test(x)));
const dupCount = computed(() => tokens.value.length - ids.value.length);
const validIds = computed(() => ids.value.filter((x) => /^\d{6,25}$/.test(x)));
const changed = computed(() => diffRules(prompt.value, defaultPrompt.value));
const isDefaultPrompt = computed(() => !changed.value.length);
const canSubmit = computed(() => cred.value && validIds.value.length && !badIds.value.length && !submitting.value);
const blocker = computed(() => {
  if (!validIds.value.length) return '请输入视频素材 ID';
  if (badIds.value.length) return '有格式不正确的 ID';
  if (!cred.value) return '请完成第 2 步登录';
  return '';
});

onMounted(async () => {
  try {
    const p = await api('/api/prompts/default');
    defaultPrompt.value = p.content;
    defaultVersion.value = p.version;
    prompt.value = p.content;
  } catch (e) {
    error.value = e.message;
  }
});

function removeBad() {
  idsText.value = validIds.value.join('\n');
}

async function submit() {
  error.value = '';
  submitting.value = true;
  try {
    const r = await api('/api/tasks', {
      method: 'POST',
      body: { name: name.value, material_ids: validIds.value, prompt_text: prompt.value, ...cred.value },
    });
    if (r.videos.some((v) => v.status === 'resolve_failed')) {
      result.value = r;
    } else {
      toast(`已创建任务，共 ${r.videos.length} 个视频，正在后台处理`);
      router.push(`/tasks/${r.id}`);
    }
  } catch (e) {
    error.value = e.message;
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="page narrow">
    <h1>新建打点任务</h1>

    <div v-if="result" class="panel">
      <h2>任务已创建，部分素材未能解析</h2>
      <table class="list">
        <tr v-for="v in result.videos" :key="v.id">
          <td class="mono">{{ v.material_id }}</td>
          <td>{{ v.name || '—' }}</td>
          <td>
            <span v-if="v.status === 'resolve_failed'" class="err small">{{ v.error }}</span>
            <span v-else class="tag queued">已加入处理队列</span>
          </td>
        </tr>
      </table>
      <div class="row" style="margin-top: 12px">
        <router-link :to="`/tasks/${result.id}`"><button class="primary">查看任务</button></router-link>
      </div>
    </div>

    <template v-else>
      <section class="panel step">
        <div class="step-head"><span class="num">1</span><h2>要处理哪些视频？</h2></div>
        <textarea v-model="idsText" rows="6" class="mono ids" autofocus
          placeholder="粘贴视频素材 ID，每行一个，也可以用逗号或空格分隔&#10;例如：&#10;2577528631920178630&#10;2577528631249483206" />
        <div class="row small id-stat">
          <span v-if="validIds.length" class="tag done">{{ validIds.length }} 个视频</span>
          <span v-else class="muted">还没有输入 ID</span>
          <span v-if="dupCount" class="muted">已自动去除 {{ dupCount }} 个重复</span>
          <span v-if="badIds.length" class="err">
            {{ badIds.length }} 个格式不正确：{{ badIds.slice(0, 3).join('、') }}{{ badIds.length > 3 ? '…' : '' }}
            <button class="sm link" @click="removeBad">移除</button>
          </span>
          <span class="spacer" />
          <span class="muted">素材 ID 可在内容生产系统「素材管理」列表中复制</span>
        </div>
        <label class="name-field">
          <span class="muted small">任务名称（选填）</span>
          <input v-model="name" maxlength="200" placeholder="不填则使用创建时间" />
        </label>
      </section>

      <section class="panel step">
        <div class="step-head"><span class="num">2</span><h2>登录内容生产系统</h2>
          <span class="muted small">用于把素材 ID 换成视频地址，每个任务登录一次</span></div>
        <CtpLogin @change="cred = $event" />
      </section>

      <section class="panel step rule" @click="drawer = true">
        <div class="step-head"><span class="num">3</span><h2>抽题规则</h2></div>
        <div class="rule-body">
          <div>
            <template v-if="isDefaultPrompt">
              <b>使用默认规则</b><span v-if="defaultVersion" class="muted small"> · v{{ defaultVersion }}</span>
              <div class="muted small">{{ summarize(prompt) }}</div>
            </template>
            <template v-else>
              <b>已为本任务自定义规则</b>
              <div class="small" style="color: var(--warn)">修改了：{{ changed.join('、') }}</div>
            </template>
          </div>
          <span class="spacer" />
          <button class="sm" @click.stop="drawer = true">{{ isDefaultPrompt ? '查看 / 调整' : '继续修改' }}</button>
          <button v-if="!isDefaultPrompt" class="sm link" @click.stop="prompt = defaultPrompt">恢复默认</button>
        </div>
      </section>
    </template>

    <div v-if="!result" class="submit-bar">
      <div class="page narrow row">
        <span v-if="error" class="err">{{ error }}</span>
        <span v-else-if="blocker" class="muted">{{ blocker }}</span>
        <span v-else class="muted">将处理 {{ validIds.length }} 个视频，预计每个 1～2 分钟</span>
        <span class="spacer" />
        <button class="primary big" :disabled="!canSubmit" @click="submit">
          {{ submitting ? '正在解析素材…' : `创建任务${validIds.length ? `（${validIds.length}）` : ''}` }}
        </button>
      </div>
    </div>

    <RulesDrawer v-model:open="drawer" v-model="prompt" :default-text="defaultPrompt" />
  </div>
</template>

<style scoped>
.narrow { max-width: 920px; }
.page.narrow { padding-bottom: 120px; }
.step { padding: 18px 20px; }
.step-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.step-head h2 { margin: 0; }
.num { width: 22px; height: 22px; border-radius: 50%; background: var(--primary); color: #fff; font-size: 12px; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
.ids { font-size: 14px; line-height: 1.7; }
.id-stat { margin-top: 8px; flex-wrap: wrap; }
.name-field { display: flex; flex-direction: column; gap: 4px; margin-top: 14px; max-width: 420px; }
.rule { cursor: pointer; transition: border-color .15s; }
.rule:hover { border-color: var(--primary); }
.rule-body { display: flex; align-items: center; gap: 10px; padding-left: 32px; }
.submit-bar { position: fixed; left: 0; right: 0; bottom: 0; background: #fff; border-top: 1px solid var(--border); box-shadow: 0 -4px 16px rgba(0, 0, 0, .05); z-index: 20; }
.submit-bar .page { padding-top: 12px; padding-bottom: 12px; }
button.big { height: 38px; padding: 0 22px; font-size: 14px; }
</style>
