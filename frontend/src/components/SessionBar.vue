<script setup lang="ts">
// 会话条：session_id + API Key 都可运行时修改（localStorage 持久化）。
// session_id 是 checkpointer 的 thread_id——同一 ID 跨请求延续状态，
// 澄清/审批恢复全凭它；API Key 401/429 时在这里换 key 不用重启。
// 两个字段都走 emit 上报，组件自身无状态（受控组件）。
import BaseButton from './base/BaseButton.vue'

defineProps<{
  sessionId: string
  apiKey: string
  running: boolean
}>()
const emit = defineEmits<{
  (e: 'update:sessionId', v: string): void
  (e: 'update:apiKey', v: string): void
  (e: 'reset'): void
}>()
</script>

<template>
  <div class="session-bar">
    <label class="field">
      <span>会话 ID <em>checkpointer thread_id</em></span>
      <input
        :value="sessionId"
        placeholder="demo-001"
        @input="emit('update:sessionId', ($event.target as HTMLInputElement).value)"
      />
    </label>
    <label class="field">
      <span>API Key <em>X-API-Key 头，白名单鉴权</em></span>
      <input
        :value="apiKey"
        placeholder="dev-key-001"
        @input="emit('update:apiKey', ($event.target as HTMLInputElement).value)"
      />
    </label>
    <BaseButton variant="ghost" size="sm" :disabled="running" @click="emit('reset')">
      清空本轮
    </BaseButton>
  </div>
</template>

<style scoped>
.session-bar {
  display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap;
  padding: 12px 14px; background: var(--panel); border: 1px solid var(--border);
  border-radius: var(--radius-m);
}
.field { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 200px; }
.field span { font-size: 11px; color: var(--text-faint); letter-spacing: 1px; display: flex; gap: 6px; align-items: baseline; }
.field span em { font-style: normal; font-size: 10.5px; color: var(--text-faint); opacity: 0.75; font-family: var(--mono); }
.field input { font-family: var(--mono); font-size: 12.5px; padding: 6px 10px; }
</style>
