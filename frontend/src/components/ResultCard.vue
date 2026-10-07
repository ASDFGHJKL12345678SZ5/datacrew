<script setup lang="ts">
// 结果面板：结论 / SQL / 数据 / 图表 四标签页。
// 为什么用标签页而不是纵向堆叠：结论、SQL、50 行数据、图表四张卡纵深太长，
// 用户真正要的"结论"被埋底；标签页让默认落点永远是结论，其余按需展开。
// 图表标签仅在 chart_url 存在时出现（没有图表就不放空 Tab）。
import { computed, ref } from 'vue'
import Badge from './base/Badge.vue'
import ChartImage from './ChartImage.vue'
import RowTable from './RowTable.vue'
import MarkdownBlock from './MarkdownBlock.vue'
import type { ResultEvent } from '@/types/events'

const props = defineProps<{ result: ResultEvent; apiKey: string }>()
const active = ref('conclusion')

const tabs = computed(() => {
  const t = [{ id: 'conclusion', label: '结论' }]
  if (props.result.sql) t.push({ id: 'sql', label: 'SQL' })
  if (props.result.columns && props.result.columns.length > 0) t.push({ id: 'data', label: '数据' })
  if (props.result.chart_url) t.push({ id: 'chart', label: '图表' })
  return t
})
// 结果变了（新一轮）回到结论页：上新结果时用户第一眼要看结论
const copied = ref(false)
async function copySql() {
  if (!props.result.sql) return
  try {
    await navigator.clipboard.writeText(props.result.sql)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch {
    /* 剪贴板不可用（非安全上下文）：静默，不打扰 */
  }
}
</script>

<template>
  <div class="result rise">
    <div class="result-head">
      <Badge tone="ok">✓ 查询完成</Badge>
      <div class="stats">
        <span v-if="result.row_count != null" class="stat"><b>{{ result.row_count }}</b> 行</span>
        <span v-if="result.retry_count" class="stat warn" title="SQL 报错后自动回灌修正的次数">自愈 <b>{{ result.retry_count }}</b> 次</span>
        <span v-if="result.sql" class="stat">七道闸后执行</span>
      </div>
    </div>

    <div class="card result-card">
      <div class="tabs" role="tablist" aria-label="结果视图">
        <button
          v-for="t in tabs" :key="t.id"
          class="tab" :class="{ active: active === t.id }"
          role="tab" :aria-selected="active === t.id"
          @click="active = t.id"
        >{{ t.label }}</button>
      </div>

      <div v-if="active === 'conclusion'" class="tab-pane">
        <MarkdownBlock :content="result.summary" />
      </div>

      <div v-if="active === 'sql'" class="tab-pane">
        <div class="pane-head">
          <span class="faint">经安全闸校验后实际执行的 SQL</span>
          <button class="btn btn-ghost btn-sm" @click="copySql">{{ copied ? '已复制 ✓' : '复制' }}</button>
        </div>
        <pre>{{ result.sql }}</pre>
      </div>

      <div v-if="active === 'data'" class="tab-pane">
        <RowTable :columns="result.columns" :rows="result.rows" />
      </div>

      <div v-if="active === 'chart'" class="tab-pane">
        <ChartImage :url="result.chart_url" :api-key="apiKey" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.result { display: flex; flex-direction: column; gap: 12px; }
.result-head { display: flex; justify-content: space-between; align-items: center; padding: 4px 2px; flex-wrap: wrap; gap: 8px; }
.stats { display: flex; gap: 16px; }
.stat { font-size: 12px; color: var(--text-dim); font-family: var(--mono); }
.stat b { color: var(--text); font-size: 15px; margin-right: 2px; }
.stat.warn b { color: var(--warn); }
.result-card { padding: 0; overflow: hidden; }
.tabs {
  display: flex; gap: 2px; padding: 10px 14px 0;
  border-bottom: 1px solid var(--border);
}
.tab {
  border: none; background: transparent; color: var(--text-faint);
  font-size: 13px; font-weight: 500; padding: 8px 16px; border-radius: 8px 8px 0 0;
  border-bottom: 2px solid transparent; margin-bottom: -1px;
  transition: color .15s, border-color .15s, background .15s;
}
.tab:hover { color: var(--text-dim); background: var(--panel-2); }
.tab.active { color: var(--accent); border-bottom-color: var(--accent); background: var(--accent-soft); }
.tab-pane { padding: 16px 20px 18px; }
.pane-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
</style>
