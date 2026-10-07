<script setup lang="ts">
// 执行时间线：把 reducer 累积的 node_done 序列渲染成排查询链。
// 数据价值：executor 出现多次 = 报错自愈重试，insight_writer 前的
// sql_generator×2 一眼可见——"系统在怎么努力的"全在这里。
// 展示规则（中文名/尝试序号/延迟人性化/detail 扁平化）在 services/timeline.ts
// 纯函数里，组件只渲染；那里有单测锁行为。
import { computed } from 'vue'
import { buildTimelineRows, formatLatency, totalLatency } from '@/services/timeline'
import type { TimelineEntry } from '@/services/reducer'

const props = defineProps<{ entries: TimelineEntry[] }>()
const rows = computed(() => buildTimelineRows(props.entries))
const total = computed(() => totalLatency(rows.value))
</script>

<template>
  <div v-if="rows.length > 0" class="timeline">
    <div class="tl-head">
      <span class="tl-title">执行轨迹</span>
      <span class="tl-meta">{{ rows.length }} 节点 · 合计 {{ formatLatency(total) }}</span>
    </div>
    <ol class="tl-list">
      <li v-for="row in rows" :key="row.key" class="tl-row">
        <span class="tl-dot" :class="row.tone" :title="row.node" />
        <div class="tl-body">
          <div class="tl-line">
            <span class="tl-label">{{ row.label }}</span>
            <span v-if="row.attempt > 1" class="tl-retry" :title="'第 ' + row.attempt + ' 次执行（前次失败后重试/自愈）'">
              ×{{ row.attempt }}
            </span>
            <span class="tl-node mono">{{ row.node }}</span>
            <span class="tl-latency mono">{{ formatLatency(row.latency_ms) }}</span>
          </div>
          <div v-if="row.detail.length > 0" class="tl-detail">
            <span v-for="[k, v] in row.detail" :key="k" class="tl-chip">
              <b>{{ k }}</b>{{ v }}
            </span>
          </div>
        </div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.timeline {
  background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-l);
  padding: 14px 18px;
}
.tl-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
.tl-title { font-size: 11px; letter-spacing: 2px; color: var(--text-faint); text-transform: uppercase; font-weight: 600; }
.tl-meta { font-size: 11.5px; color: var(--text-faint); font-family: var(--mono); }
.tl-list { list-style: none; margin: 0; padding: 0; }
.tl-row { display: flex; gap: 12px; padding: 7px 0; position: relative; }
/* 连接线：行与行之间画竖向导轨，一眼看出先后顺序 */
.tl-row:not(:last-child)::before {
  content: ''; position: absolute; left: 4px; top: 23px; bottom: -7px;
  width: 1px; background: var(--border-strong);
}
.tl-dot {
  width: 9px; height: 9px; border-radius: 50%; flex: none; margin-top: 7px;
  background: var(--accent); box-shadow: 0 0 8px rgba(79, 140, 255, 0.5);
}
.tl-dot.cyan { background: var(--cyan); box-shadow: 0 0 8px rgba(47, 216, 240, 0.5); }
.tl-dot.ok { background: var(--ok); box-shadow: 0 0 8px rgba(61, 220, 151, 0.5); }
.tl-dot.accent-2 { background: var(--accent-2); box-shadow: 0 0 8px rgba(124, 108, 255, 0.5); }
.tl-dot.warn { background: var(--warn); box-shadow: 0 0 8px rgba(251, 191, 36, 0.5); }
.tl-body { flex: 1; min-width: 0; }
.tl-line { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.tl-label { font-size: 13.5px; font-weight: 600; }
.tl-retry {
  font-family: var(--mono); font-size: 10.5px; color: var(--warn);
  border: 1px solid rgba(251, 191, 36, 0.4); background: var(--warn-soft);
  border-radius: 5px; padding: 0 6px; cursor: help;
}
.tl-node { font-size: 11px; color: var(--text-faint); }
.tl-latency { margin-left: auto; font-size: 12px; color: var(--text-dim); }
.tl-detail { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 3px; }
.tl-chip {
  display: inline-flex; gap: 5px; font-size: 11px; font-family: var(--mono);
  color: var(--text-dim); background: var(--card-2); border-radius: 5px; padding: 0 7px;
  max-width: 100%;
}
.tl-chip b { color: var(--text-faint); font-weight: 600; }
</style>
