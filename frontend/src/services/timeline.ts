// ===== 执行时间线格式化（纯函数） =====
// 输入 reducer 累积的 node_done 序列，输出可直接渲染的行模型。
// 为什么单列：时间线要显示"尝试序号"（同节点第几次出现 = 自愈重试）、
// 节点中文名、延迟人性化、detail 扁平化——这些都是展示规则，属于可单测的
// 纯逻辑，不该散在组件的 computed 里。
import type { TimelineEntry } from '@/services/reducer'

/** 节点名 → 人话。未登记的新节点原样透传（后端加节点前端不瞎）。 */
export const NODE_LABELS: Record<string, string> = {
  schema_curator: 'Schema 检索',
  sql_generator: 'SQL 生成',
  executor: 'SQL 执行',
  insight_writer: '结论撰写',
  clarification: '口径澄清',
  approval: '人工审批',
}

/** 节点 → 时间线圆点颜色（与状态语义一致：执行绿/生成蓝/澄清审批黄）。 */
export const NODE_TONES: Record<string, string> = {
  schema_curator: 'cyan',
  sql_generator: 'accent',
  executor: 'ok',
  insight_writer: 'accent-2',
  clarification: 'warn',
  approval: 'warn',
}

export interface TimelineRow {
  /** 稳定 key：同节点多次出现也要能区分（node#attempt） */
  key: string
  node: string
  label: string
  tone: string
  /** 该节点第几次执行（1 起）。>1 即自愈/人工重试，UI 需要显式标注 */
  attempt: number
  latency_ms?: number
  detail: [string, string][]
}

/** 延迟人性化：<1s 显示 ms，否则秒。无延迟显示占位。 */
export function formatLatency(ms?: number): string {
  if (ms == null || Number.isNaN(ms)) return '—'
  if (ms < 1000) return Math.round(ms) + 'ms'
  return (ms / 1000).toFixed(1) + 's'
}

/** detail 键 → 中文标签（未登记的键原样透传：后端加字段前端不瞎）。 */
export const DETAIL_LABELS: Record<string, string> = {
  clarified: '已澄清',
  preference_applied: '沿用偏好',
  tables: '命中表',
  retry: '重试次数',
  retries_exhausted: '重试耗尽',
  approval: '审批',
  error: '错误类型',
  row_count: '行数',
  chart: '图表',
}

/** detail 扁平化为可渲染的 [k, v] 对：只保留标量，嵌套对象/数组截断成 JSON 摘要。 */
export function flattenDetail(detail?: Record<string, unknown>, maxLen = 48): [string, string][] {
  if (!detail) return []
  const out: [string, string][] = []
  for (const [k, v] of Object.entries(detail)) {
    if (v === null || v === undefined) continue
    let s: string
    if (typeof v === 'object') s = JSON.stringify(v)
    else s = String(v)
    if (s.length > maxLen) s = s.slice(0, maxLen - 1) + '…'
    out.push([DETAIL_LABELS[k] ?? k, s])
  }
  return out
}

/** 时间线总耗时（各节点延迟之和；无延迟字段时返回 undefined）。 */
export function totalLatency(rows: TimelineRow[]): number | undefined {
  if (rows.length === 0) return undefined
  const nums = rows.map((r) => r.latency_ms).filter((n): n is number => typeof n === 'number')
  if (nums.length === 0) return undefined
  return nums.reduce((a, b) => a + b, 0)
}

export function buildTimelineRows(entries: TimelineEntry[]): TimelineRow[] {
  const seen = new Map<string, number>()
  return entries.map((e) => {
    const n = (seen.get(e.node) ?? 0) + 1
    seen.set(e.node, n)
    return {
      key: e.node + '#' + n,
      node: e.node,
      label: NODE_LABELS[e.node] ?? e.node,
      tone: NODE_TONES[e.node] ?? 'accent',
      attempt: n,
      latency_ms: e.latency_ms,
      detail: flattenDetail(e.detail),
    }
  })
}
