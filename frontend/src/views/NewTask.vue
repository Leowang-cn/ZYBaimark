<script setup>
import { computed, inject, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { api } from '../api';
import CtpLogin from '../components/CtpLogin.vue';
import PromptEditor from '../components/PromptEditor.vue';

const router = useRouter();
const toast = inject('toast');
const name = ref('');
const idsText = ref('');
const prompt = ref('');
const defaultPrompt = ref('');
const cred = ref(null);
const submitting = ref(false);
const error = ref('');
const result = ref(null);

const ids = computed(() => [...new Set(idsText.value.split(/[\s,，;；]+/).filter(Boolean))]);
const badIds = computed(() => ids.value.filter((x) => !/^\d{6,25}$/.test(x)));

onMounted(async () => {
  try {
    const p = await api('/api/prompts/default');
    defaultPrompt.value = p.content;
    prompt.value = p.content;
  } catch (e) {
    error.value = e.message;
  }
});

async function submit() {
  error.value = '';
  submitting.value = true;
  try {
    const r = await api('/api/tasks', {
      method: 'POST',
      body: { name: name.value, material_ids: ids.value, prompt_text: prompt.value, ...cred.value },
    });
    const failed = r.videos.filter((v) => v.status === 'resolve_failed');
    if (failed.length) {
      result.value = r;
    } else {
      toast(`已创建任务，共 ${r.videos.length} 个视频`);
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
  <div class="page">
    <h1>新建任务</h1>

    <div v-if="result" class="panel">
      <h2>任务已创建，但部分素材解析失败</h2>
      <table class="list">
        <tr v-for="v in result.videos" :key="v.id">
          <td class="mono">{{ v.material_id }}</td>
          <td>{{ v.name || '—' }}</td>
          <td><span class="tag" :class="v.status">{{ v.status === 'resolve_failed' ? v.error : '已加入队列' }}</span></td>
        </tr>
      </table>
      <div class="row" style="margin-top: 12px">
        <router-link :to="`/tasks/${result.id}`"><button class="primary">查看任务</button></router-link>
      </div>
    </div>

    <template v-else>
      <div class="panel">
        <h2>1. 登录内容生产系统</h2>
        <p class="muted small" style="margin-top: -6px">每个任务登录一次，只用于把素材 ID 解析为视频地址。</p>
        <CtpLogin @change="cred = $event" />
      </div>

      <div class="panel">
        <h2>2. 视频素材 ID</h2>
        <label class="field">
          <span>任务名称（可选）</span>
          <input v-model="name" maxlength="200" placeholder="默认使用创建时间" style="width: 360px" />
        </label>
        <label class="field">
          <span>素材 ID（每行一个，或用逗号/空格分隔）</span>
          <textarea v-model="idsText" rows="5" class="mono" placeholder="2577528631920178630&#10;2577528631249483206" />
        </label>
        <p class="small muted">共 {{ ids.length }} 个 <span v-if="badIds.length" class="err">；格式不正确：{{ badIds.join('、') }}</span></p>
      </div>

      <div class="panel">
        <h2>3. 抽题规则</h2>
        <p class="muted small" style="margin-top: -6px">默认使用当前默认规则；在这里修改只对本任务生效。</p>
        <PromptEditor v-model="prompt" :default-text="defaultPrompt" />
      </div>

      <div class="panel row">
        <p v-if="error" class="err" style="margin: 0">{{ error }}</p>
        <span class="spacer" />
        <span v-if="!cred" class="muted small">请先完成登录</span>
        <button class="primary" :disabled="!cred || !ids.length || badIds.length > 0 || prompt.length < 20 || submitting" @click="submit">
          {{ submitting ? '正在解析素材…' : '创建任务' }}
        </button>
      </div>
    </template>
  </div>
</template>
