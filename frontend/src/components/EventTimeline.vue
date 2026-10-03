<script setup lang="ts">
// 节点时间线：node_done 事件的可视化（trace 的跨轮累积在这里只显示本轮）。
// executor 反复出现 = 自愈重试——排查询链一眼可见。
import type { TimelineEntry } from '@/services/reducer'

defineProps<{ timeline: TimelineEntry[] }>()

const NODE_LABEL: Record<string, string> = {
  schema_curator: '模式检索与口径分析',
  sql_generator: 'SQL 生成',
  executor: '安全执行（七道闸）',
  insight_writer: '洞察与图表',
}

function labelOf(node: string): string {
  return NODE_LABEL[node] ?? node
}

function detailText(detail?: Record<string, unknown>): string {
  if (!detail) return ''
  return Object.entries(detail)
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join('  ')
}
</script>

<template>
  <div v-if="timeline.length > 0" class="card timeline">
    <h3>节点时间线 <span class="muted">（executor 多次出现 = 自愈重试）</span></h3>
    <div v-for="(t, i) in timeline" :key="i" class="row">
      <span class="step">{{ i + 1 }}</span>
      <span class="node">{{ labelOf(t.node) }}</span>
      <span v-if="t.latency_ms != null" class="latency">{{ t.latency_ms }} ms</span>
      <span v-if="detailText(t.detail)" class="detail">{{ detailText(t.detail) }}</span>
    </div>
  </div>
</template>

<style scoped>
h3 { margin: 0 0 10px; font-size: 14px; }
.row { display: flex; gap: 12px; align-items: baseline; padding: 5px 0; border-bottom: 1px dashed #ffffff10; }
.row:last-child { border-bottom: none; }
.step {
  width: 20px; height: 20px; border-radius: 50%; background: var(--accent);
  color: #fff; font-size: 11px; display: grid; place-items: center; flex: none;
}
.node { font-weight: 600; min-width: 170px; }
.latency { color: var(--ok); font-size: 12px; font-family: monospace; }
.detail { color: var(--text-muted); font-size: 12px; word-break: break-all; }
</style>
