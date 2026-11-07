<script setup>
// 抽题规则编辑器：支持恢复默认、与默认规则对比
import { computed, ref } from 'vue';

const props = defineProps({
  modelValue: { type: String, default: '' },
  defaultText: { type: String, default: '' },
  rows: { type: Number, default: 16 },
});
const emit = defineEmits(['update:modelValue']);
const showDiff = ref(false);

const changed = computed(() => props.defaultText && props.modelValue.trim() !== props.defaultText.trim());

const diff = computed(() => {
  const a = props.defaultText.split('\n');
  const b = props.modelValue.split('\n');
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) { out.push({ t: ' ', s: a[i] }); i++; j++; }
    else if (j < m && (i >= n || dp[i][j + 1] >= dp[i + 1][j])) { out.push({ t: '+', s: b[j++] }); }
    else { out.push({ t: '-', s: a[i++] }); }
  }
  return out.filter((x, k, arr) => x.t !== ' ' || arr.slice(Math.max(0, k - 2), k + 3).some((y) => y.t !== ' '));
});
</script>

<template>
  <div>
    <div class="row small" style="margin-bottom: 6px">
      <span v-if="changed" class="tag warn">已修改</span>
      <span v-else class="tag">与默认规则一致</span>
      <span class="spacer" />
      <button class="sm" :disabled="!changed" @click="showDiff = !showDiff">{{ showDiff ? '收起对比' : '与默认对比' }}</button>
      <button class="sm" :disabled="!changed" @click="emit('update:modelValue', defaultText)">恢复默认</button>
    </div>
    <textarea :value="modelValue" :rows="rows" class="mono small"
      @input="emit('update:modelValue', $event.target.value)" />
    <pre v-if="showDiff && changed" class="diff small"><template v-for="(d, k) in diff" :key="k"><span :class="d.t === '+' ? 'add' : d.t === '-' ? 'del' : ''">{{ d.t }} {{ d.s }}
</span></template></pre>
  </div>
</template>

<style scoped>
.diff { max-height: 240px; overflow: auto; background: #fafafa; border: 1px solid var(--border); border-radius: 6px; padding: 8px; white-space: pre-wrap; }
.add { background: #e6ffec; color: #116329; }
.del { background: #ffebe9; color: #a40e26; }
</style>
