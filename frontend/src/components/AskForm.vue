<script setup lang="ts">
// 提问命令栏：示例问题覆盖三条路径（正常问 / 模糊追问 / 大表审批），方便演示。
import { ref } from 'vue'

const props = defineProps<{ disabled: boolean }>()
const emit = defineEmits<{ (e: 'submit', v: string): void }>()
const question = ref('')

const EXAMPLES = [
  { q: '上个月各渠道实付销售额是多少', kind: 'ok', label: '正常问' },
  { q: '销售额是多少', kind: 'warn', label: '澄清' },
  { q: '流量日志表一共有多少行记录', kind: 'accent', label: '审批' },
]

function submit() {
  const q = question.value.trim()
  if (q && !props.disabled) emit('submit', q)
}
</script>

<template>
  <div class="ask-form">
    <div class="cmd">
      <span class="endpoint">POST <b>/ask</b></span>
      <textarea
        v-model="question"
        rows="1"
        placeholder="用自然语言提问业务指标，例如：上个月各渠道实付销售额是多少"
        @keydown.enter.exact.prevent="submit"
      />
      <button class="btn btn-primary" :disabled="disabled || !question.trim()" @click="submit">
        <span v-if="disabled" class="spin" />
        {{ disabled ? '执行中' : '提问' }}
      </button>
    </div>
    <div class="examples">
      <span class="faint">试试：</span>
      <button v-for="e in EXAMPLES" :key="e.q" class="chip" :class="e.kind" :disabled="disabled" @click="question = e.q">
        <b>{{ e.label }}</b>{{ e.q }}
      </button>
      <span class="faint enter-hint"><span class="kbd">Enter</span> 提交 · <span class="kbd">Shift+Enter</span> 换行</span>
    </div>
  </div>
</template>

<style scoped>
.ask-form { display: flex; flex-direction: column; gap: 10px; }
.cmd {
  display: flex; align-items: stretch; gap: 10px;
  background: var(--panel); border: 1px solid var(--border-strong); border-radius: 14px;
  padding: 10px 10px 10px 14px; box-shadow: var(--shadow);
  transition: border-color .15s;
}
.cmd:focus-within { border-color: var(--accent); }
.endpoint {
  display: flex; align-items: center; font-family: var(--mono); font-size: 12px;
  color: var(--text-faint); white-space: nowrap;
}
.endpoint b { color: var(--cyan); font-weight: 600; }
.cmd textarea {
  border: none; background: transparent; box-shadow: none; resize: none;
  font-size: 14px; padding: 8px 0; min-height: 24px;
}
.cmd textarea:focus { box-shadow: none; }
.cmd button { align-self: center; padding: 9px 22px; }
.examples { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 12px; }
.chip {
  display: inline-flex; align-items: center; gap: 7px;
  background: var(--panel); border: 1px solid var(--border); color: var(--text-dim);
  border-radius: 999px; padding: 3px 13px; font-size: 12px; transition: all .15s;
}
.chip b {
  font-family: var(--mono); font-size: 10px; padding: 0 6px; border-radius: 4px;
  background: var(--card-2); color: var(--text-dim);
}
.chip:hover:not(:disabled) { color: var(--text); border-color: var(--border-strong); background: var(--card); }
.chip.warn b { color: var(--warn); }
.chip.ok b { color: var(--ok); }
.chip.accent b { color: var(--accent); }
.chip:disabled { opacity: .5; cursor: not-allowed; }
.enter-hint { margin-left: auto; }
</style>
