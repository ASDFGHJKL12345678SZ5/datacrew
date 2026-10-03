<script setup lang="ts">
// 会话与鉴权：session_id 可编辑（改它就开新会话）；API Key 运行时注入（不烧进产物）。
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
      <span>会话 ID</span>
      <input
        :value="sessionId"
        placeholder="同一 ID 的状态跨请求延续"
        @input="emit('update:sessionId', ($event.target as HTMLInputElement).value)"
      />
    </label>
    <label class="field key">
      <span>API Key</span>
      <input
        :value="apiKey"
        type="password"
        placeholder="dev-key-001"
        @input="emit('update:apiKey', ($event.target as HTMLInputElement).value)"
      />
    </label>
    <button class="btn btn-ghost" :disabled="running" @click="emit('reset')">清空本轮</button>
  </div>
</template>

<style scoped>
.session-bar {
  display: flex; gap: 14px; align-items: flex-end; flex-wrap: wrap;
  padding: 12px 14px; background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
}
.field { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 180px; }
.field span { font-size: 11px; color: var(--text-faint); letter-spacing: 1px; }
.field.key { max-width: 220px; }
.field input { font-family: var(--mono); font-size: 12.5px; padding: 6px 10px; }
</style>
