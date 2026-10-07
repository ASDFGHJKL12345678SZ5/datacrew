import { describe, expect, it } from 'vitest'
import { buildTimelineRows, flattenDetail, formatLatency, totalLatency } from '@/services/timeline'

describe('formatLatency', () => {
  it('毫秒/秒人性化 + 缺失占位', () => {
    expect(formatLatency(320)).toBe('320ms')
    expect(formatLatency(1500)).toBe('1.5s')
    expect(formatLatency(undefined)).toBe('—')
    expect(formatLatency(NaN)).toBe('—')
  })
})

describe('flattenDetail', () => {
  it('只保留标量，null/undefined 跳过', () => {
    expect(flattenDetail({ n: 3, ok: true, skip: null, gone: undefined })).toEqual([
      ['n', '3'],
      ['ok', 'true'],
    ])
  })

  it('嵌套对象转 JSON 摘要，超长截断带省略号', () => {
    const rows = flattenDetail({ obj: { a: 1 }, long: 'x'.repeat(60) })
    expect(rows[0]).toEqual(['obj', '{"a":1}'])
    expect(rows[1][0]).toBe('long')
    expect(rows[1][1].endsWith('…')).toBe(true)
    expect(rows[1][1].length).toBeLessThanOrEqual(48)
  })

  it('已知 detail 键翻译成中文标签，未知键原样透传', () => {
    expect(flattenDetail({ preference_applied: '实付销售额', unknown_key: 'x' })).toEqual([
      ['沿用偏好', '实付销售额'],
      ['unknown_key', 'x'],
    ])
  })

  it('空/缺失 detail 返回空数组', () => {
    expect(flattenDetail(undefined)).toEqual([])
    expect(flattenDetail({})).toEqual([])
  })
})

describe('buildTimelineRows', () => {
  it('同节点多次出现编 attempt 序号（自愈重试可见）', () => {
    const rows = buildTimelineRows([
      { node: 'executor', latency_ms: 100 },
      { node: 'sql_generator', latency_ms: 50 },
      { node: 'executor', latency_ms: 90 },
    ])
    expect(rows.map((r) => r.attempt)).toEqual([1, 1, 2])
    expect(rows[0].key).toBe('executor#1')
    expect(rows[2].key).toBe('executor#2')
    expect(rows[2].label).toBe('SQL 执行')
  })

  it('未登记节点原样透传（后端加节点前端不瞎）', () => {
    const rows = buildTimelineRows([{ node: 'brand_new_node' }])
    expect(rows[0].label).toBe('brand_new_node')
    expect(rows[0].tone).toBe('accent')
  })

  it('totalLatency 汇总有延迟字段的行，全无则 undefined', () => {
    const rows = buildTimelineRows([
      { node: 'executor', latency_ms: 100 },
      { node: 'sql_generator' },
      { node: 'insight_writer', latency_ms: 250 },
    ])
    expect(totalLatency(rows)).toBe(350)
    expect(totalLatency(buildTimelineRows([{ node: 'executor' }]))).toBeUndefined()
    expect(totalLatency([])).toBeUndefined()
  })
})
