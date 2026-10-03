<script setup lang="ts">
// 结果面板：洞察（Markdown）+ SQL + 行数 + 图表 + 自愈次数。
import ChartImage from './ChartImage.vue'
import RowTable from './RowTable.vue'
import MarkdownBlock from './MarkdownBlock.vue'
import type { ResultEvent } from '@/types/events'

defineProps<{ result: ResultEvent; apiKey: string }>()
</script>

<template>
  <div class="result rise">
    <div class="result-head">
      <span class="tag ok">✓ 查询完成</span>
      <div class="stats">
        <span v-if="result.row_count != null" class="stat"><b>{{ result.row_count }}</b> 行</span>
        <span v-if="result.retry_count" class="stat warn"><b>{{ result.retry_count }}</b> 次自愈</span>
        <span v-if="result.sql" class="stat"><b>7</b> 道闸后执行</span>
      </div>
    </div>

    <div class="card insight">
      <div class="card-title">业务结论</div>
      <MarkdownBlock :content="result.summary" />
    </div>

    <div v-if="result.sql" class="card sql-card">
      <div class="card-title">执行的 SQL</div>
      <pre>{{ result.sql }}</pre>
    </div>

    <div class="card">
      <div class="card-title">结果数据 <span class="faint" v-if="result.row_count != null">· 共 {{ result.row_count }} 行</span></div>
      <RowTable :columns="result.columns" :rows="result.rows" />
    </div>

    <div class="card">
      <div class="card-title">图表</div>
      <ChartImage :url="result.chart_url" :api-key="apiKey" />
    </div>
  </div>
</template>

<style scoped>
.result { display: flex; flex-direction: column; gap: 12px; }
.result-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 4px 2px;
}
.stats { display: flex; gap: 16px; }
.stat { font-size: 12px; color: var(--text-dim); font-family: var(--mono); }
.stat b { color: var(--text); font-size: 15px; margin-right: 2px; }
.stat.warn b { color: var(--warn); }
.card-title {
  font-size: 11px; letter-spacing: 2px; color: var(--text-faint);
  text-transform: uppercase; font-weight: 600; margin-bottom: 10px;
}
.insight { border-left: 3px solid var(--ok); }
.sql-card pre { margin: 0; }
</style>
