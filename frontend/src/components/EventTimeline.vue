<script setup lang="ts">
// 执行轨迹：node_done 事件的控制台视图。executor 反复出现 = 自愈重试——排查询链一眼可见。
import { computed } from 'vue'
import type { TimelineEntry } from '@/services/reducer'

const props = defineProps<{ timeline: TimelineEntry[] }>()

const NODE_META: Record<string, { label: string; icon: string; tone: string }> = {
  schema_curator: { label: '模式检索与口径分析', icon: '⌕', tone: 'cyan' },
  sql_generator: { label: 'SQL 生成（CoT）', icon: '✎', tone: 'accent' },
  executor: { label: '安全执行 · 七道闸', icon: '▤', tone: 'ok' },
  insight_writer: { label: '洞察与图表', icon: '◈', tone: 'warn' },
}

const rows = computed(() =>
  props.timeline.map((t) => {
    const meta = NODE_META[t.node] ?? { label: t.node, icon: '•', tone: '' }
    return { ...t, ...meta }
  }),
)

// 累计耗时：给一个"这轮查询总时长"的体感
const totalMs = computed(() =>
  props.timeline.reduce((s, t) => s + (t.latency_ms ?? 0), 0),
)

function detailText(detail?: Record<string, unknown>): string {
  if (!detail) return ''
  return Object.entries(detail)
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join('  ')
}
</script>

<template>
  <div v-if="timeline.length > 0" class="card trace">
    <div class="trace-head">
      <h3>执行轨迹</h3>
      <div class="trace-meta">
        <span class="faint">executor 多次出现 = 自愈重试</span>
        <span class="tag ok">合计 {{ totalMs }} ms</span>
      </div>
    </div>
    <div class="log">
      <div v-for="(t, i) in rows" :key="i" class="log-row rise">
        <span class="seq">{{ String(i + 1).padStart(2, '0') }}</span>
        <span class="node-icon" :class="t.tone">{{ t.icon }}</span>
        <span class="node-label">{{ t.label }}</span>
        <span v-if="t.latency_ms != null" class="latency">{{ t.latency_ms }}ms</span>
        <span v-if="detailText(t.detail)" class="detail">{{ detailText(t.detail) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trace-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.trace-head h3 { margin: 0; font-size: 14px; letter-spacing: 1px; }
.trace-meta { display: flex; gap: 12px; align-items: center; font-size: 12px; }
.log {
  background: #060a13; border: 1px solid var(--border); border-radius: 10px;
  padding: 8px 6px; font-family: var(--mono); font-size: 12px;
}
.log-row {
  display: flex; gap: 10px; align-items: baseline; padding: 6px 10px; border-radius: 7px;
}
.log-row:hover { background: rgba(79, 140, 255, 0.05); }
.seq { color: var(--text-faint); flex: none; }
.node-icon { flex: none; width: 18px; text-align: center; font-weight: 700; }
.node-icon.cyan { color: var(--cyan); }
.node-icon.accent { color: var(--accent); }
.node-icon.ok { color: var(--ok); }
.node-icon.warn { color: var(--warn); }
.node-label { color: var(--text); white-space: nowrap; }
.latency { color: var(--ok); flex: none; }
.detail { color: var(--text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
