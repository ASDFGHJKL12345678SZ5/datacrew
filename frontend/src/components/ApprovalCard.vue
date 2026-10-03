<script setup lang="ts">
// 审批卡片：大表无过滤查询被闸挂起 → 人批准/拒绝 → {approved} 经参数回传。
// 注意：批准/拒绝这个按钮就是"谁有权置位 approved"的架构答案——人在环路里。
import { ref } from 'vue'

defineProps<{ reason: string; sql: string }>()
const emit = defineEmits<{ (e: 'decide', v: boolean): void }>()

const showSql = ref(true)
</script>

<template>
  <div class="card approval">
    <div class="head">
      <span class="tag warn">人工审批 · 大表查询</span>
      <button class="link" @click="showSql = !showSql">{{ showSql ? '收起 SQL' : '查看 SQL' }}</button>
    </div>
    <p class="reason">{{ reason }}</p>
    <pre v-if="showSql">{{ sql }}</pre>
    <div class="actions">
      <button class="btn btn-ok" @click="emit('decide', true)">✓ 批准执行</button>
      <button class="btn btn-err" @click="emit('decide', false)">✗ 拒绝</button>
    </div>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; }
.tag.warn { color: var(--warn); border-color: var(--warn); }
.link { background: none; border: none; color: var(--accent); font-size: 12px; }
.reason { color: var(--text-muted); font-size: 13px; }
.actions { display: flex; gap: 10px; margin-top: 6px; }
.approval { border-color: var(--warn); }
</style>
