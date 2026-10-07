<script setup lang="ts">
// 审批卡片：大表无过滤查询被闸挂起 → 人批准/拒绝 → {approved} 经参数回传。
// 这两个按钮就是"谁有权置位 approved"的架构答案——人在环路里。
// SQL 默认展开：审批前必须让人看清即将执行什么（安全语境下默认透明）。
import { ref } from 'vue'
import BaseButton from './base/BaseButton.vue'

defineProps<{ reason: string; sql: string }>()
const emit = defineEmits<{ (e: 'decide', v: boolean): void }>()
const showSql = ref(true)
</script>

<template>
  <div class="interrupt approval rise">
    <div class="head">
      <span class="tag warn">⏸ 人工介入 · 大表查询审批</span>
      <BaseButton variant="ghost" size="sm" @click="showSql = !showSql">
        {{ showSql ? '收起 SQL' : '查看 SQL' }}
      </BaseButton>
    </div>
    <p class="reason">{{ reason }}</p>
    <pre v-if="showSql">{{ sql }}</pre>
    <div class="actions">
      <BaseButton variant="ok" @click="emit('decide', true)">✓ 批准执行</BaseButton>
      <BaseButton variant="err" @click="emit('decide', false)">✗ 拒绝</BaseButton>
      <span class="note">批准后自动补 LIMIT 1000，审批粒度与资源边界分离</span>
    </div>
  </div>
</template>

<style scoped>
.interrupt {
  background: linear-gradient(180deg, var(--warn-soft), var(--panel));
  border: 1px solid rgba(251, 191, 36, 0.45); border-radius: var(--radius-l);
  padding: 18px 20px; box-shadow: var(--shadow);
}
.head { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.reason { color: var(--text-dim); font-size: 13px; margin: 10px 0 6px; }
.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.note { font-size: 12px; color: var(--text-faint); }
</style>
