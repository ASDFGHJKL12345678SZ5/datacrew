<script setup lang="ts">
// 结果表预览：截断展示（5 万行不值得塞进 DOM）。
import { computed } from 'vue'

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
</script>

<template>
  <div v-if="columns && columns.length > 0" class="table-wrap">
    <table>
      <thead>
        <tr><th v-for="c in columns" :key="c">{{ c }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in shownRows" :key="i">
          <td v-for="(cell2, j) in row" :key="j">{{ cell(cell2) }}</td>
        </tr>
      </tbody>
    </table>
    <p v-if="rows && rows.length > MAX_ROWS" class="faint">
      仅预览前 {{ MAX_ROWS }} 行，共 {{ rows.length }} 行
    </p>
  </div>
  <p v-else class="faint">查询未返回行数据。</p>
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
</style>
