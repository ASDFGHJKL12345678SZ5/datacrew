<script setup lang="ts">
// 会话与鉴权栏：session_id 可编辑（改它就开新会话）；API Key 运行时注入。
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
    <label>
      会话 ID
      <input
        :value="sessionId"
        placeholder="同一 ID 的状态跨请求延续"
        @input="emit('update:sessionId', ($event.target as HTMLInputElement).value)"
      />
    </label>
    <label>
      API Key（X-API-Key）
      <input
        :value="apiKey"
        type="password"
        placeholder="dev-key-001"
        @input="emit('update:apiKey', ($event.target as HTMLInputElement).value)"
      />
    </label>
    <button class="btn" :disabled="running" @click="emit('reset')">清空本轮</button>
  </div>
</template>

<style scoped>
.session-bar {
  display: flex; gap: 18px; align-items: flex-end; flex-wrap: wrap;
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 12px; padding: 12px 16px; margin-bottom: 14px;
}
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-muted); flex: 1; min-width: 200px; }
input {
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  color: var(--text); padding: 7px 11px; font: inherit; font-size: 13px;
}
input:focus { outline: none; border-color: var(--accent); }
</style>
