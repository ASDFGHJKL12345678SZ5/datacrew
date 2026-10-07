<script setup lang="ts">
// 设计系统·徽章：状态/语义标签的唯一出口。
// tone 只允许 neutral/accent/ok/warn/err ——与 style.css 的 .tag.* 一一对应，
// 新增语义先加 token 再加 tone，不允许组件里就地写颜色。
defineProps<{
  tone?: 'neutral' | 'accent' | 'ok' | 'warn' | 'err'
  mono?: boolean
}>()
</script>

<template>
  <span class="badge" :class="[tone ?? 'neutral', { mono: mono ?? true }]"><slot /></span>
</template>

<style scoped>
.badge {
  display: inline-flex; align-items: center; gap: 5px; padding: 1px 10px; border-radius: 999px;
  font-size: 12px; line-height: 1.6;
  border: 1px solid var(--border-strong); color: var(--text-dim); background: var(--panel-2);
}
.badge.mono { font-family: var(--mono); }
.badge.accent { color: var(--accent); border-color: rgba(79, 140, 255, 0.4); background: var(--accent-soft); }
.badge.ok { color: var(--ok); border-color: rgba(61, 220, 151, 0.4); background: var(--ok-soft); }
.badge.warn { color: var(--warn); border-color: rgba(251, 191, 36, 0.4); background: var(--warn-soft); }
.badge.err { color: var(--err); border-color: rgba(255, 107, 107, 0.4); background: var(--err-soft); }
</style>
