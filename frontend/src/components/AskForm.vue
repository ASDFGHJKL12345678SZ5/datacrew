<script setup lang="ts">
// 提问表单：示例问题覆盖三条路径（正常问/模糊追问/大表审批），方便演示。
import { ref } from 'vue'

const emit = defineEmits<{ (e: 'submit', v: string): void }>()
const question = ref('')

const EXAMPLES = [
  '上个月各渠道实付销售额是多少',
  '销售额是多少',
  '流量日志表一共有多少行记录',
]

function submit() {
  const q = question.value.trim()
  if (q) emit('submit', q)
}
</script>

<template>
  <div class="ask-form">
    <textarea
      v-model="question"
      rows="2"
      placeholder="问点什么…（Enter 提交，Shift+Enter 换行）"
      @keydown.enter.exact.prevent="submit"
    />
    <div class="row">
      <div class="examples">
        <span class="muted">示例：</span>
        <button v-for="e in EXAMPLES" :key="e" class="chip" @click="question = e">{{ e }}</button>
      </div>
      <button class="btn btn-primary" :disabled="!question.trim()" @click="submit">提问</button>
    </div>
  </div>
</template>

<style scoped>
textarea {
  width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
  color: var(--text); padding: 12px 14px; font: inherit; font-size: 14px; resize: vertical;
}
textarea:focus { outline: none; border-color: var(--accent); }
.row { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; gap: 10px; flex-wrap: wrap; }
.examples { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; font-size: 12px; }
.chip {
  background: var(--bg-elevated); border: 1px solid var(--border); color: var(--text-muted);
  border-radius: 20px; padding: 2px 11px; font-size: 12px;
}
.chip:hover { color: var(--text); border-color: var(--accent); }
</style>
