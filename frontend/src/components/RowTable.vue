<script setup lang="ts">
// 结果表预览：截断展示（latency_ms 26 的数量级不值得把 5 万行都塞进 DOM）。
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
    <p v-if="rows && rows.length > MAX_ROWS" class="muted">
      仅预览前 {{ MAX_ROWS }} 行，共 {{ rows.length }} 行
    </p>
  </div>
</template>

<style scoped>
.table-wrap { overflow: auto; max-height: 320px; margin: 8px 0; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; }
th, td { border: 1px solid var(--border); padding: 4px 10px; text-align: left; white-space: nowrap; }
th { background: var(--bg-elevated); position: sticky; top: 0; }
</style>
