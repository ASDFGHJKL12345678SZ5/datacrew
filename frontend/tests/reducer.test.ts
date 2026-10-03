import { describe, expect, it } from 'vitest'
import { askReducer, initialState } from '@/services/reducer'
import type { AskEvent } from '@/types/events'

const nd = (node: string, latency_ms = 10): AskEvent => ({ event: 'node_done', node, latency_ms })

describe('askReducer', () => {
  it('start 清空上一轮进入 running（对应后端每轮重置产物字段）', () => {
    let s = askReducer(initialState, { type: 'start', question: '上个月销售额' })
    s = askReducer(s, { type: 'event', ev: nd('executor') })
    s = askReducer(s, { type: 'event', ev: { event: 'result', summary: 'x', sql: 'SELECT 1', columns: ['a'], rows: [[1]] } })
    expect(s.phase).toBe('done')
    s = askReducer(s, { type: 'start', question: '新问题' })
    expect(s.phase).toBe('running')
    expect(s.timeline).toHaveLength(0)
    expect(s.result).toBeUndefined()
  })

  it('node_done 累积进时间线（自愈重试出现同节点多次也保留）', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, { type: 'event', ev: nd('executor', 20) })
    s = askReducer(s, { type: 'event', ev: nd('sql_generator', 5) })
    s = askReducer(s, { type: 'event', ev: nd('executor', 18) })
    expect(s.timeline.map((t) => t.node)).toEqual(['executor', 'sql_generator', 'executor'])
    expect(s.timeline[2].latency_ms).toBe(18)
  })

  it('澄清事件进入 clarifying 并带出问题与选项', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, { type: 'event', ev: { event: 'clarification', type: 'clarification', question: '哪个口径？', options: ['实付', 'GMV'] } })
    expect(s.phase).toBe('clarifying')
    expect(s.clarification?.options).toEqual(['实付', 'GMV'])
  })

  it('审批事件进入 approving 并带出 SQL', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, { type: 'event', ev: { event: 'approval', type: 'approval', reason: '大表无过滤', sql: 'SELECT COUNT(*) FROM biz.traffic_logs' } })
    expect(s.phase).toBe('approving')
    expect(s.approval?.sql).toContain('traffic_logs')
  })

  it('error 事件进入 error 并保留消息（后端 error_type 文案透传）', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, { type: 'event', ev: { event: 'error', message: '用户拒绝了该查询' } })
    expect(s.phase).toBe('error')
    expect(s.error).toBe('用户拒绝了该查询')
  })

  it('result 后仍来的 node_done 不冲掉终态', () => {
    let s = askReducer(initialState, { type: 'start', question: 'q' })
    s = askReducer(s, { type: 'event', ev: { event: 'result', summary: '答', sql: 'SELECT 1', columns: [], rows: [] } })
    s = askReducer(s, { type: 'event', ev: nd('executor') })
    expect(s.phase).toBe('done')
    expect(s.timeline).toHaveLength(1)
  })
})
