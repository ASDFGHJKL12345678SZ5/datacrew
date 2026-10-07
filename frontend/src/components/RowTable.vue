<script setup lang="ts">
// 结果表预览：截断展示（5 万行不值得塞进 DOM）。行号列 + 数字列右对齐，
// 等宽字体保证扫读时不跳列。空结果走设计系统 EmptyState：说清"为什么空"。
import { computed } from 'vue'
import EmptyState from './base/EmptyState.vue'

const props = defineProps<{
  columns: string[] | null
  rows: unknown[][] | null
}>()

const MAX_ROWS = 50
const shownRows = computed(() => (props.rows ?? []).slice(0, MAX_ROWS))

function cell(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
function isNumeric(v: unknown): boolean {
  return typeof v === 'number' || (typeof v === 'string' && v !== '' && !Number.isNaN(Number(v)))
}
</script>

<template>
  <div v-if="columns && columns.length > 0" class="table-wrap">
    <table>
      <thead>
        <tr>
          <th class="rownum">#</th>
          <th v-for="c in columns" :key="c">{{ c }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in shownRows" :key="i">
          <td class="rownum">{{ i + 1 }}</td>
          <td v-for="(v, j) in row" :key="j" :class="{ num: isNumeric(v) }">{{ cell(v) }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="rows && rows.length > MAX_ROWS" class="faint trunc-note">
      仅预览前 {{ MAX_ROWS }} 行，共 {{ rows.length }} 行
    </p>
  </div>
  <EmptyState v-else icon="▤" title="本次查询没有返回行数据" description="SQL 经七道闸后执行成功，但结果集为空——过滤条件未命中任何行，可调整时间范围或口径再试。" />
</template>

<style scoped>
.table-wrap { overflow: auto; max-height: 340px; border: 1px solid var(--border); border-radius: 10px; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; font-family: var(--mono); }
th, td { border-bottom: 1px solid var(--border); padding: 7px 12px; text-align: left; white-space: nowrap; }
th {
  background: var(--panel-2); position: sticky; top: 0; color: var(--text-dim);
  font-weight: 600; letter-spacing: 0.5px;
}
tbody tr:hover { background: var(--accent-soft); }
td { color: var(--text); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.rownum { color: var(--text-faint); width: 1%; user-select: none; }
.trunc-note { margin: 8px 2px 0; font-size: 12px; }
</style>
