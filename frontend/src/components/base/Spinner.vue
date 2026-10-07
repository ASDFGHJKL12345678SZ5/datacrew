<script setup lang="ts">
// 设计系统·加载指示：仅旋转动画，语义由使用方文案承载（aria-hidden）。
defineProps<{ size?: number }>()
</script>

<template>
  <span class="base-spinner" :style="{ width: (size ?? 14) + 'px', height: (size ?? 14) + 'px' }" aria-hidden="true" />
</template>

<style scoped>
.base-spinner {
  display: inline-block; flex: none;
  border: 2px solid rgba(255, 255, 255, 0.35); border-top-color: #fff; border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
</style>
