<script setup lang="ts">
// 结果面板：洞察（Markdown）+ SQL + 行数 + 图表 + 自愈次数。
import ChartImage from './ChartImage.vue'
import RowTable from './RowTable.vue'
import MarkdownBlock from './MarkdownBlock.vue'
import type { ResultEvent } from '@/types/events'

defineProps<{ result: ResultEvent; apiKey: string }>()
</script>

<template>
  <div class="card result">
    <MarkdownBlock :content="result.summary" />
    <div class="meta">
      <span v-if="result.row_count != null" class="tag">{{ result.row_count }} 行</span>
      <span v-if="result.retry_count" class="tag warn">自愈 {{ result.retry_count }} 次</span>
      <span v-if="result.sql" class="tag">闸后执行</span>
    </div>
    <div v-if="result.sql" class="sql-block">
      <div class="sql-head">执行的 SQL（已过七道闸）</div>
      <pre>{{ result.sql }}</pre>
    </div>
    <RowTable :columns="result.columns" :rows="result.rows" />
    <ChartImage :url="result.chart_url" :api-key="apiKey" />
  </div>
</template>

<style scoped>
.meta { display: flex; gap: 8px; margin: 8px 0; }
.tag.warn { color: var(--warn); border-color: var(--warn); }
.sql-head { font-size: 12px; color: var(--text-muted); margin-top: 6px; }
.result { border-color: #22c55e40; }
</style>
