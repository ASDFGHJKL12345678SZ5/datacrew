<script setup lang="ts">
// 会话条：session_id 可编辑（同一 ID 的状态跨请求延续，澄清/审批都靠它）。
// API Key 不在这里暴露——默认内置 dev-key-001，需要覆盖的人改构建期 env。
defineProps<{
  sessionId: string
  running: boolean
}>()
const emit = defineEmits<{
  (e: 'update:sessionId', v: string): void
  (e: 'reset'): void
}>()
</script>

<template>
  <div class="session-bar">
    <label class="field">
      <span>会话 ID</span>
      <input
        :value="sessionId"
        placeholder="demo-001"
        @input="emit('update:sessionId', ($event.target as HTMLInputElement).value)"
      />
    </label>
    <button class="btn btn-ghost" :disabled="running" @click="emit('reset')">清空本轮</button>
  </div>
</template>

<style scoped>
.session-bar {
  display: flex; gap: 14px; align-items: flex-end;
  padding: 10px 14px; background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
}
.field { display: flex; flex-direction: column; gap: 4px; flex: 1; }
.field span { font-size: 11px; color: var(--text-faint); letter-spacing: 1px; }
.field input { font-family: var(--mono); font-size: 12.5px; padding: 6px 10px; max-width: 260px; }
</style>
