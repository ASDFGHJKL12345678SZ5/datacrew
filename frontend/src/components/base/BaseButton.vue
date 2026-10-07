<script setup lang="ts">
// 设计系统·按钮：variant 决定语义色，loading 时自转并禁用。
// 存在理由：以前每个组件手写 class 与禁用条件，改一处漏三处；
// 现在按钮的语义（primary=主操作/ok=批准/err=拒绝/ghost=次要）只有这一份定义。
defineProps<{
  variant?: 'default' | 'primary' | 'ok' | 'err' | 'ghost'
  size?: 'sm' | 'md'
  loading?: boolean
  disabled?: boolean
  type?: 'button' | 'submit'
}>()
const emit = defineEmits<{ (e: 'click', ev: MouseEvent): void }>()
</script>

<template>
  <button
    class="base-btn"
    :class="[variant ?? 'default', size ?? 'md', { loading: loading }]"
    :disabled="disabled || loading"
    :type="type ?? 'button'"
    @click="emit('click', $event)"
  >
    <span v-if="loading" class="base-btn-spin" aria-hidden="true" />
    <slot />
  </button>
</template>

<style scoped>
.base-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 7px;
  border: 1px solid var(--border-strong); background: var(--panel-2); color: var(--text);
  border-radius: var(--radius-s); padding: 7px 16px; font-size: 13px; font-weight: 500;
  font-family: var(--sans); line-height: 1.5;
  transition: border-color .15s, background .15s, transform .05s, filter .15s;
  white-space: nowrap;
}
.base-btn:hover:not(:disabled) { border-color: var(--accent); background: var(--card-2); }
.base-btn:active:not(:disabled) { transform: translateY(1px); }
.base-btn:disabled { opacity: .45; cursor: not-allowed; }
.base-btn.sm { padding: 4px 12px; font-size: 12px; border-radius: 7px; }
.base-btn.primary {
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  border-color: transparent; color: #fff; font-weight: 600;
  box-shadow: 0 2px 14px rgba(79, 140, 255, 0.35);
}
.base-btn.primary:hover:not(:disabled) { filter: brightness(1.12); }
.base-btn.ok { background: var(--ok); border-color: var(--ok); color: #052e20; font-weight: 700; }
.base-btn.ok:hover:not(:disabled) { filter: brightness(1.08); }
.base-btn.err { background: transparent; border-color: var(--err); color: var(--err); }
.base-btn.err:hover:not(:disabled) { background: var(--err-soft); }
.base-btn.ghost { background: transparent; border-color: var(--border); color: var(--text-dim); }
.base-btn-spin {
  width: 12px; height: 12px; flex: none;
  border: 2px solid rgba(255,255,255,0.35); border-top-color: #fff; border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
</style>
