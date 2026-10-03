<script setup lang="ts">
// 审批卡片：大表无过滤查询被闸挂起 → 人批准/拒绝 → {approved} 经参数回传。
// 这两个按钮就是"谁有权置位 approved"的架构答案——人在环路里。
import { ref } from 'vue'

defineProps<{ reason: string; sql: string }>()
const emit = defineEmits<{ (e: 'decide', v: boolean): void }>()
const showSql = ref(true)
</script>

<template>
  <div class="card interrupt approval rise">
    <div class="head">
      <span class="tag warn">⏸ 人工介入 · 大表查询审批</span>
      <button class="btn btn-ghost" style="padding: 3px 10px; font-size: 12px;" @click="showSql = !showSql">
        {{ showSql ? '收起 SQL' : '查看 SQL' }}
      </button>
    </div>
    <p class="reason">{{ reason }}</p>
    <pre v-if="showSql">{{ sql }}</pre>
    <div class="actions">
      <button class="btn btn-ok" @click="emit('decide', true)">✓ 批准执行</button>
      <button class="btn btn-err" @click="emit('decide', false)">✗ 拒绝</button>
      <span class="faint note">批准后自动补 LIMIT 1000，审批粒度与资源边界分离</span>
    </div>
  </div>
</template>

<style scoped>
.approval { border-color: rgba(251, 191, 36, 0.45); background: linear-gradient(180deg, rgba(251,191,36,0.05), var(--panel)); }
.head { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.reason { color: var(--text-dim); font-size: 13px; margin: 10px 0 6px; }
.actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.note { font-size: 12px; }
</style>
