<script setup lang="ts">
// 设计系统·空态：图标 + 标题 + 描述 + 操作位。空状态是产品的一部分，
// 不是"没数据"三个字——每个空态都要告诉用户下一步做什么。
defineProps<{ icon?: string; title: string; description?: string }>()
</script>

<template>
  <div class="empty-state">
    <div v-if="icon" class="empty-icon" aria-hidden="true">{{ icon }}</div>
    <p class="empty-title">{{ title }}</p>
    <p v-if="description" class="empty-desc">{{ description }}</p>
    <div v-if="$slots.default" class="empty-actions"><slot /></div>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 36px 20px; text-align: center;
  border: 1px dashed var(--border-strong); border-radius: var(--radius-l);
  background: rgba(11, 18, 32, 0.4);
}
.empty-icon { font-size: 26px; line-height: 1; opacity: 0.8; }
.empty-title { margin: 4px 0 0; font-size: 14px; font-weight: 600; color: var(--text-dim); }
.empty-desc { margin: 0; font-size: 12.5px; color: var(--text-faint); max-width: 420px; line-height: 1.7; }
.empty-actions { margin-top: 10px; display: flex; gap: 10px; }
</style>
