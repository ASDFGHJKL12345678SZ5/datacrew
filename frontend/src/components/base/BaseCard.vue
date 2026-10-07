<script setup lang="ts">
// 设计系统·卡片：统一的面板容器。title 走命名 slot（带左侧色条），
// 也可以整个自定义头部。padding 密度可调（dense）。
defineProps<{ title?: string; tone?: 'default' | 'ok' | 'warn' | 'err' | 'accent'; dense?: boolean }>()
</script>

<template>
  <section class="base-card" :class="[tone ?? 'default', { dense: dense }]">
    <header v-if="title || $slots.actions" class="base-card-head">
      <div class="base-card-title">
        <span v-if="tone && tone !== 'default'" class="title-bar" aria-hidden="true" />
        <slot name="title">{{ title }}</slot>
      </div>
      <div v-if="$slots.actions" class="base-card-actions"><slot name="actions" /></div>
    </header>
    <slot />
  </section>
</template>

<style scoped>
.base-card {
  background: linear-gradient(180deg, var(--card), var(--panel));
  border: 1px solid var(--border); border-radius: var(--radius-l); padding: 18px 20px;
  box-shadow: var(--shadow);
}
.base-card.dense { padding: 13px 16px; }
.base-card.ok { border-left: 3px solid var(--ok); }
.base-card.warn { border-left: 3px solid var(--warn); }
.base-card.err { border-left: 3px solid var(--err); }
.base-card.accent { border-left: 3px solid var(--accent); }
.base-card-head {
  display: flex; justify-content: space-between; align-items: center; gap: 10px;
  margin-bottom: 10px;
}
.base-card-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 11px; letter-spacing: 2px; color: var(--text-faint);
  text-transform: uppercase; font-weight: 600;
}
.title-bar { width: 3px; height: 12px; border-radius: 2px; background: currentColor; }
.base-card-actions { display: flex; gap: 8px; align-items: center; }
</style>
